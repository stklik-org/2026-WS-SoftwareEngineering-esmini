"""Schema of `GET /v1/info` (PLAN.md FR-08)."""

from __future__ import annotations

from pydantic import Field

from app.models.common import ResponseModel


class EsminiInfo(ResponseModel):
    version: str = Field(examples=["3.8.1"])
    git_revision: str | None = Field(default=None, examples=["v3.8.1-0-19d26b68"])
    build: str | None = Field(default=None, examples=["6383"])


class Capabilities(ResponseModel):
    """Which features this image offers. Clients should check these instead of assuming them."""

    simulations: bool = True
    # Explicit aliases: the camelCase generator would produce "rendering2D".
    rendering2d: bool = Field(default=False, alias="rendering2d", description="2D top-view snapshots (planned).")
    rendering3d: bool = Field(
        default=False, alias="rendering3d", description="3D viewer snapshots (planned, optional)."
    )
    road_previews: bool = Field(default=False, description="`POST /v1/road-previews`: road network as PNG.")
    videos: bool = Field(default=False, description="`video` in `config.outputs`: MP4 of a run in the 3D viewer.")
    permutations: bool = Field(default=False, description="Parameter-distribution runs (planned).")
    parameter_overrides: bool = Field(default=False, description="ParameterDeclaration overrides (planned).")
    osi: bool = Field(default=False, description="OSI trace output (not planned for v1).")


class LimitsInfo(ResponseModel):
    max_upload_mb: float
    max_total_upload_mb: float
    default_timeout_seconds: float
    max_timeout_seconds: float
    max_concurrent_jobs: int
    max_queued_jobs: int
    job_ttl_seconds: float = Field(description="Jobs and their artefacts are deleted after this many seconds.")
    max_video_seconds: float = Field(description="A video covers at most this much simulation time.")
    video_timeout_seconds: float = Field(description="Wall-clock limit for rendering one video.")
    preview_timeout_seconds: float = Field(description="Wall-clock limit for each step of a road preview.")


class InfoResponse(ResponseModel):
    service: str = "esmini-rest"
    service_version: str
    image_version: str
    api_version: str = "v1"
    esmini: EsminiInfo
    arch: str = Field(description="CPU architecture of the container, e.g. `amd64`.", examples=["amd64"])
    capabilities: Capabilities
    limits: LimitsInfo
