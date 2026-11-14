"""Bearer-token ASGI middleware shared by GigBid's MCP servers."""
from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable


class BearerAuth:
    """Checks `Authorization: Bearer <token>` in constant time.

    require_user: also demand X-GigBid-User (used for per-user rate limits and audit).
    health: optional async callable for an unauthenticated GET /healthz.
    """

    def __init__(self, app, token: str, *, require_user: bool = False,
                 health: Callable[[], Awaitable[dict]] | None = None):
        if not token:
            raise ValueError("bearer token must be non-empty")
        self.app, self.expected = app, f"Bearer {token}".encode()
        self.require_user, self.health = require_user, health

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse

        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/healthz":
            body = await self.health() if self.health else {"ok": True}
            return await JSONResponse(body)(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        if not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        if self.require_user and not headers.get(b"x-gigbid-user"):
            return await JSONResponse({"error": "X-GigBid-User header required"},
                                      status_code=400)(scope, receive, send)
        return await self.app(scope, receive, send)
