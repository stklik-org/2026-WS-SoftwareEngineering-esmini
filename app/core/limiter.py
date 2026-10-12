"""Admission limit for synchronous work such as road previews (jobs have their own, in `JobStore`)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from app.core.errors import ApiError


class AdmissionLimiter:
    """At most `max_running` callers at once and `max_waiting` waiting; any further caller gets 429."""

    def __init__(self, max_running: int, max_waiting: int) -> None:
        self._semaphore = asyncio.Semaphore(max_running)
        self._limit = max_running + max_waiting
        self._admitted = 0

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._admitted >= self._limit:
            raise ApiError(429, "too_many_requests", "The service is busy; retry later.", headers={"Retry-After": "5"})
        self._admitted += 1
        try:
            async with self._semaphore:
                yield
        finally:
            self._admitted -= 1
