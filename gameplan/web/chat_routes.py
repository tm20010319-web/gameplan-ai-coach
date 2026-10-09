from gameplan.web.account_routes import private_access
"""Text conversation endpoints shared by both existing application servers."""
import asyncio
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from gameplan.paths import ROOT
from gameplan.ai import knowledge_chat
from gameplan.core import chat_store as legacy_chat_store
from gameplan.persistence.database import StoreProxy, enabled
chat_store = StoreProxy(legacy_chat_store, "gameplan.persistence.chats")


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    revision: int = Field(ge=0)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("请输入问题")
        return value.strip()


def process_message(sid, message, received_at):
    token = None
    try:
        job = chat_store.begin(sid, message.request_id, message.revision, message.text)
        if "cached" in job:
            return {"session": job["state"], "response": job["cached"]}
        token, state = job["token"], job["state"]
        history = [{"question": turn["user"], "answer": turn["response"]["answer"]} for turn in state["turns"][-4:]]
        if enabled():
            from gameplan.persistence.profiles import read as read_profile
            profile = read_profile()
            result = knowledge_chat.answer(message.text, state["context"], history, received_at=received_at, profile=profile["data"])
            result["profile_version"] = profile["version"]
        else:
            result = knowledge_chat.answer(message.text, state["context"], history, received_at=received_at)
        updated = chat_store.finish(sid, token, message.request_id, message.text, result)
        return {"session": updated, "response": updated["turns"][-1]["response"]}
    finally:
        if token:
            chat_store.release(sid, token)


def routes(require_local):
    router = APIRouter()

    @router.get("/chat")
    def page():
        return FileResponse(ROOT / "static/chat.html")

    @router.post("/api/chat/sessions")
    def create(request: Request):
        private_access(request, require_local)
        return chat_store.create()

    @router.get("/api/chat/sessions/{sid}")
    def session(sid: str, request: Request):
        private_access(request, require_local)
        try:
            return chat_store.read(sid)
        except KeyError:
            raise HTTPException(404, "对话不存在，请开始新对话。")

    @router.post("/api/chat/sessions/{sid}/messages")
    async def message(sid: str, payload: Message, request: Request):
        private_access(request, require_local)
        try:
            return await asyncio.to_thread(process_message, sid, payload, time.time())
        except KeyError:
            raise HTTPException(404, "对话不存在，请开始新对话。")
        except chat_store.ChatConflict as exc:
            raise HTTPException(409, str(exc))
        except Exception:
            # Do not return model response bodies, keys, user paths or config errors.
            raise HTTPException(503, "本次回答未完成，请重试；已有对话已保留。")

    return router
