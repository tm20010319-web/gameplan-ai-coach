"""A server-trusted identity seam; no client-supplied user id grants access."""
import asyncio
import inspect
import os
from contextlib import suppress, asynccontextmanager
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from gameplan.paths import ROOT
from gameplan.persistence import database, profiles


def private_access(request, require_local):
    if database.enabled() and getattr(request.state, "authenticated_user", False):
        return
    require_local(request)


def authorize(target):
    actor = database.owner()
    try:
        target = str(UUID(target))
    except ValueError:
        raise HTTPException(404, "玩家不存在。")
    if database.redis_enabled():
        from gameplan.persistence.redis_accounts import authorize as redis_authorize
        try:
            return redis_authorize(target)
        except KeyError:
            raise HTTPException(404, '玩家不存在。')
        except PermissionError:
            raise HTTPException(403, '没有查看这位玩家的权限。')
    with database.connection() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=%s AND active", (target,)).fetchone():
            raise HTTPException(404, "玩家不存在。")
        if actor != target:
            allowed = db.execute("""SELECT 1 FROM user_roles r WHERE r.user_id=%s AND
                (r.role='admin' OR (r.role='coach' AND EXISTS (
                    SELECT 1 FROM coach_assignments WHERE coach_id=r.user_id AND player_id=%s)))""", (actor, target)).fetchone()
            if not allowed:
                raise HTTPException(403, "没有查看这位玩家的权限。")
            db.execute("INSERT INTO access_audit(actor_id,target_id,action) VALUES(%s,%s,'read_profile')", (actor, target))
    return target


def install(app, require_local):
    @app.middleware("http")
    async def identity(request, call_next):
        if not database.enabled():
            return await call_next(request)
        # Public static/catalog reads do not need a database identity.
        protected = request.url.path.startswith(("/api/chat/", "/api/reviews", "/api/profile", "/api/accounts", "/api/me", "/api/matches", "/api/vision/", "/api/monitor/", "/api/screen/", "/api/coach/", "/api/bp/", "/api/in_game/", "/api/post_game/"))
        if not protected or request.url.path in ("/api/monitor/status", "/api/vision/status", "/api/coach/status"):
            return await call_next(request)
        try:
            origin = request.headers.get("origin")
            if origin and (urlparse(origin).scheme, urlparse(origin).netloc) != (request.url.scheme, request.url.netloc):
                raise HTTPException(403, "不允许跨站访问玩家数据。")
            resolver = getattr(app.state, "resolve_user", None)
            user_id = resolver(request) if resolver else None
            if inspect.isawaitable(user_id):
                user_id = await user_id
            is_dev = False
            if not user_id and os.getenv("GAMEPLAN_DEV_USER_ID"):
                require_local(request)
                user_id = os.environ["GAMEPLAN_DEV_USER_ID"]
                is_dev = True
            if not user_id:
                raise HTTPException(401, "请先登录。")
            uid = str(UUID(str(user_id)))
            with database.as_user(uid):
                def exists():
                    if database.redis_enabled():
                        from gameplan.persistence.redis_accounts import exists as redis_exists
                        return redis_exists(uid)
                    with database.connection() as db:
                        return db.execute("SELECT 1 FROM users WHERE id=%s AND active", (uid,)).fetchone()
                if not await asyncio.to_thread(exists):
                    raise HTTPException(401, "用户不存在或已停用。")
                request.state.development_user = is_dev
                request.state.authenticated_user = not is_dev
                return await call_next(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except (ValueError, PermissionError):
            return JSONResponse({"detail": "登录身份无效。"}, status_code=401)
        except KeyError:
            return JSONResponse({'detail': '记录不存在。'}, status_code=404)

    router = APIRouter()

    def ready():
        if not database.enabled():
            raise HTTPException(503, "新数据库尚未配置。")

    @router.get("/profile")
    def page():
        return FileResponse(ROOT / "static" / "profile.html")

    @router.get("/api/me")
    def me(request: Request):
        ready()
        if database.redis_enabled():
            from gameplan.persistence.redis_accounts import me as redis_me
            return {**redis_me(), 'development_user': getattr(request.state, 'development_user', False)}
        with database.connection() as db:
            user = db.execute("SELECT id,display_name FROM users WHERE id=%s", (database.owner(),)).fetchone()
            roles = [r["role"] for r in db.execute("SELECT role FROM user_roles WHERE user_id=%s", (database.owner(),)).fetchall()]
        return {**user, "roles": roles, "development_user": getattr(request.state, "development_user", False)}

    @router.get("/api/accounts/players")
    def players():
        ready()
        if database.redis_enabled():
            from gameplan.persistence.redis_accounts import players as redis_players
            return {'items': redis_players()}
        with database.connection() as db:
            return {"items": db.execute("""SELECT u.id,u.display_name FROM users u WHERE u.active AND (
                u.id=%s OR EXISTS(SELECT 1 FROM user_roles WHERE user_id=%s AND role='admin') OR
                EXISTS(SELECT 1 FROM user_roles r JOIN coach_assignments a ON a.coach_id=r.user_id
                    WHERE r.user_id=%s AND r.role='coach' AND a.player_id=u.id)) ORDER BY u.created_at LIMIT 100""",
                (database.owner(), database.owner(), database.owner())).fetchall()}

    @router.get("/api/profile")
    def profile(user_id: str | None = None):
        ready()
        target = authorize(user_id or database.owner())
        return {**profiles.read(target), "jobs": profiles.jobs(target)}

    @router.get("/api/profile/versions")
    def versions(user_id: str | None = None, limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0)):
        ready()
        return {"items": profiles.versions(authorize(user_id or database.owner()), limit, offset)}

    @router.get("/api/profile/evidence")
    def evidence(scope: str, key: str, user_id: str | None = None):
        ready()
        return {"items": profiles.evidence(scope, key, authorize(user_id or database.owner()))}

    @router.post("/api/profile/jobs/{job_id}/retry")
    def retry(job_id: str):
        ready()
        return {"queued": profiles.retry(job_id)}

    @router.get("/api/chat/sessions/{sid}/history")
    def history(sid: str, before: int | None = Query(None, gt=0), limit: int = Query(50, ge=1, le=100)):
        ready()
        from gameplan.persistence import chats
        try:
            return chats.messages(sid, before, limit)
        except KeyError:
            raise HTTPException(404, "对话不存在。")

    app.include_router(router)

    async def start_workers():
        if database.enabled():
            from gameplan.ai.profile_worker import run
            if database.redis_enabled():
                from gameplan.persistence.redis_store import client, key
                if not client().exists(key('schema_migrations')):
                    raise RuntimeError('Redis migration required; run scripts/migrate-to-redis.py')
                app.state.profile_workers = [asyncio.create_task(run()) for _ in range(2)]
                return
            # Fail startup rather than silently reconnecting to the old SQLite data.
            with database.connection() as db:
                installed = {r["version"] for r in db.execute("SELECT version FROM schema_migrations").fetchall()}
                expected = {p.stem for p in (ROOT / "gameplan/persistence/migrations").glob("*.sql")}
                if not expected <= installed:
                    raise RuntimeError("Database migrations required; run scripts/migrate-database.py")
            app.state.profile_workers = [asyncio.create_task(run()) for _ in range(2)]

    async def stop_workers():
        for task in getattr(app.state, "profile_workers", []):
            task.cancel()
        for task in getattr(app.state, "profile_workers", []):
            with suppress(asyncio.CancelledError):
                await task

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application) as state:
            await start_workers()
            try:
                yield state
            finally:
                await stop_workers()

    app.router.lifespan_context = lifespan
