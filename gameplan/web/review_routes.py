from gameplan.web.account_routes import private_access
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from gameplan.core import review_store as legacy_store
from gameplan.persistence.database import StoreProxy, stream_key
store = StoreProxy(legacy_store, "gameplan.persistence.reviews")
from gameplan.monitoring import review_runtime


def routes(require_local):
    router = APIRouter(prefix="/api/reviews", dependencies=[])

    @router.get("")
    def history(request: Request, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
        private_access(request, require_local)
        review_runtime.recover()
        return store.history(limit, offset)

    @router.get("/streams/{stream_id}")
    def current(stream_id: str, request: Request):
        private_access(request, require_local)
        review_runtime.recover()
        return {"match": review_runtime.snapshot(stream_id)}

    @router.get("/frames/{frame_id}")
    def frame(frame_id: str, request: Request):
        private_access(request, require_local)
        try:
            return Response(store.image(frame_id), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
        except KeyError:
            raise HTTPException(404, "未找到这张关键画面。")

    @router.post("/streams/{stream_id}/close")
    def close(stream_id: str, request: Request):
        private_access(request, require_local)
        from gameplan.monitoring.monitor_runtime import reset_match
        reset_match(stream_key(stream_id))
        store.close_stream(stream_id)
        return {"closed": True}

    @router.get("/{match_id}")
    def detail(match_id: str, request: Request):
        private_access(request, require_local)
        review_runtime.recover()
        try:
            return store.read(match_id, include_frames=True)
        except KeyError:
            raise HTTPException(404, "未找到这局复盘。")

    @router.post("/{match_id}/retry")
    def retry(match_id: str, request: Request):
        private_access(request, require_local)
        try:
            review_runtime.schedule(match_id, retry=True)
            return store.read(match_id)
        except KeyError:
            raise HTTPException(404, "未找到这局复盘。")

    return router
