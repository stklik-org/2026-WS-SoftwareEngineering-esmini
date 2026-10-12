"""ASGI middleware that caps the size of request bodies (PLAN.md §6.7, uploads)."""

from __future__ import annotations

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body


class BodySizeLimitMiddleware:
    """Rejects requests whose body exceeds `max_bytes` with 413.

    A declared Content-Length is checked before any byte is read. Bodies without a Content-Length (chunked
    transfer) are counted while they stream; the overflow is raised as a FastAPI HTTPException because FastAPI
    re-raises those unchanged from its body parser, where other exceptions would become a generic 400.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _message(self) -> str:
        return f"The request body exceeds the limit of {self.max_bytes / (1024 * 1024):g} MB."

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            response = JSONResponse(
                error_body("payload_too_large", self._message()), status_code=413, headers={"Connection": "close"}
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail=self._message())
            return message

        await self.app(scope, limited_receive, send)
