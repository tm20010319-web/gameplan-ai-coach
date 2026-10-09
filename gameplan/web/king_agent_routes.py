"""Private service-token endpoint for Root; existing pages and game behavior are reused."""
import hmac
import os
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gameplan.persistence import database, king_sessions
from gameplan.web.chat_routes import Message, chat_store, process_message


class RootIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=256)
    user_id: str = Field(min_length=1, max_length=256)

    @field_validator("tenant_id", "user_id")
    @classmethod
    def valid_identity(cls, value):
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid identity")
        return value


class RootQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=1000)
    identity: RootIdentity

    @field_validator("query")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


router = APIRouter(prefix="/api/king-agent", tags=["Root integration"])


@router.post("/query")
def query(body: RootQuery, authorization: str | None = Header(default=None)):
    token = os.getenv("KING_AGENT_SERVICE_TOKEN", "")
    if len(token) < 32:
        raise HTTPException(503, "王者助手内部接口尚未配置。")
    supplied = authorization or ""
    if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "内部服务认证失败。")
    if not database.enabled():
        raise HTTPException(503, "王者助手用户数据库尚未配置。")
    try:
        uid, sid = king_sessions.ensure(body.identity.tenant_id, body.identity.user_id, body.session_id)
        with database.as_user(uid):
            state = chat_store.read(sid)
            # Stable, valid request ids preserve Gameplan's existing retry deduplication.
            request_id = king_sessions.stable_id("sale-request-v1", body.request_id).hex
            result = process_message(sid, Message(text=body.query, request_id=request_id,
                                     revision=state["revision"]), time.time())
        response = result["response"]
        answer = response["answer"]
        status = "NEED_CLARIFICATION" if response.get("status") == "clarification" else (
            "SUCCESS" if answer else "NO_DATA")
        return {"contract_version": "agent-result-v1", "agent": "KING",
            "request_id": body.request_id, "session_id": body.session_id,
            "status": status, "answer": answer, "error_code": "",
            "data": {"chat_session_id": sid, "sources": response.get("sources", []),
                "choices": response.get("choices", []), "timers": result["session"].get("timers", []),
                "workspace_path": "/monitor", "chat_path": "/chat", "profile_path": "/profile"},
            "evidence_refs": response.get("sources", []),
            "metadata": {"gameplan_status": response.get("status"),
                         "external_status": response.get("external_status")}}
    except PermissionError:
        raise HTTPException(403, "玩家身份不可用。") from None
    except chat_store.ChatConflict:
        raise HTTPException(409, "上一条消息仍在处理，请稍后重试。") from None
    except Exception:
        raise HTTPException(503, "王者助手暂时无法完成回答，已有对话已保留。") from None
