"""The Redis workbench socket shares the HTTP server-trusted user identity."""
import inspect
import os
from ipaddress import ip_address
from urllib.parse import urlparse
from uuid import UUID
from fastapi import WebSocketDisconnect
from gameplan.persistence import database
from gameplan.persistence.redis_accounts import exists


async def serve(socket, match_id, app, connections, snapshot):
    origin = socket.headers.get('origin')
    scheme = 'https' if socket.url.scheme == 'wss' else 'http'
    if origin and (urlparse(origin).scheme, urlparse(origin).netloc) != (scheme, socket.url.netloc):
        await socket.close(code=1008)
        return
    resolver = getattr(app.state, 'resolve_websocket_user', None) or getattr(app.state, 'resolve_user', None)
    uid = resolver(socket) if resolver else None
    if inspect.isawaitable(uid):
        uid = await uid
    if not uid and os.getenv('GAMEPLAN_DEV_USER_ID'):
        try:
            local = socket.client and ip_address(socket.client.host).is_loopback
        except ValueError:
            local = False
        if not local or socket.url.hostname not in ('localhost', '127.0.0.1', '::1'):
            await socket.close(code=1008)
            return
        uid = os.environ['GAMEPLAN_DEV_USER_ID']
    try:
        uid = str(UUID(str(uid)))
        if not exists(uid):
            raise ValueError('Inactive user')
        with database.as_user(uid):
            initial = snapshot(match_id)
    except (ValueError, KeyError, PermissionError):
        await socket.close(code=1008)
        return
    identity = (uid, match_id)
    await socket.accept()
    connections[identity].add(socket)
    try:
        await socket.send_json({'type': 'snapshot', 'data': initial})
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections[identity].discard(socket)
        if not connections[identity]:
            connections.pop(identity, None)
