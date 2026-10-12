import platform
from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_services
from app.models.info import Capabilities, EsminiInfo, InfoResponse, LimitsInfo
from app.services.road_preview import previews_available
from app.services.simulation import Services, videos_available

router = APIRouter(tags=["service"])

_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


@router.get("/info", summary="Versions, capabilities and limits", response_model=InfoResponse)
async def info(services: Annotated[Services, Depends(get_services)]) -> InfoResponse:
    """Reports the esmini version inside the image, the features this image offers and the server limits."""
    settings = services.settings
    version = services.installation.version
    machine = platform.machine()
    return InfoResponse(
        service_version=__version__,
        image_version=settings.image_version,
        esmini=EsminiInfo(version=version.version, git_revision=version.git_revision, build=version.build),
        arch=_ARCH.get(machine.lower(), machine),
        capabilities=Capabilities(
            road_previews=previews_available(services), videos=videos_available(services.installation)
        ),
        limits=LimitsInfo(
            max_upload_mb=settings.max_upload_mb,
            max_total_upload_mb=settings.max_total_upload_mb,
            default_timeout_seconds=settings.default_timeout_s,
            max_timeout_seconds=settings.max_timeout_s,
            max_concurrent_jobs=settings.max_concurrent_jobs,
            max_queued_jobs=settings.max_queued_jobs,
            job_ttl_seconds=settings.job_ttl_seconds,
            max_video_seconds=settings.max_video_seconds,
            video_timeout_seconds=settings.video_timeout_s,
            preview_timeout_seconds=settings.preview_timeout_s,
        ),
    )
