from fastapi import APIRouter

from app.models.common import HealthResponse

router = APIRouter(tags=["service"])


@router.get("/health", summary="Liveness probe", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Returns `{"status": "ok"}` as soon as the service accepts requests."""
    return HealthResponse(status="ok")
