"""Uniform error model: every error response is `{code, message, details}` (PLAN.md §6.1)."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_CODES_BY_STATUS = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "too_many_requests",
}


class ApiError(Exception):
    """An error that is reported to the client with a specific status code and machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Sequence[Mapping[str, Any]] = (),
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = [dict(d) for d in details]
        self.headers = dict(headers or {})


def error_body(code: str, message: str, details: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return {"code": code, "message": message, "details": [dict(d) for d in details]}


def validation_details(errors: Iterable[Mapping[str, Any]], prefix: Sequence[str | int] = ()) -> list[dict]:
    """Convert pydantic error dicts to the public detail format; the offending input is not echoed back."""
    return [
        {"loc": [*prefix, *e.get("loc", ())], "message": e.get("msg", ""), "type": e.get("type", "")} for e in errors
    ]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = validation_details(exc.errors())
        return JSONResponse(error_body("validation_error", "The request is invalid.", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODES_BY_STATUS.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail)), status_code=exc.status_code, headers=getattr(exc, "headers", None)
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            error_body("internal_error", "An unexpected error occurred; see the container log."), status_code=500
        )
