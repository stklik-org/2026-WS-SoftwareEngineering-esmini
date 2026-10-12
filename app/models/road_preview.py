"""Configuration of `POST /v1/road-previews` (PLAN.md FR-05)."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.models.common import RequestModel


class Renderer(str, Enum):
    ODRVIEWER = "odrviewer"
    PLOT = "plot"


class RoadPreviewConfig(RequestModel):
    """Options of the road picture, a top view of the whole road network."""

    renderer: Renderer = Field(
        default=Renderer.ODRVIEWER,
        description=(
            "`odrviewer`: esmini's 3D viewer seen from above, with road surfaces, markings and objects (flat colours, "
            "no textures). `plot`: a schematic 2D drawing of every lane border with axes in metres and road ids, "
            "drawn with matplotlib."
        ),
        examples=["odrviewer"],
    )
    width: int = Field(default=1200, ge=200, le=4000, description="Image width in pixels.", examples=[1200])
    height: int = Field(default=900, ge=200, le=4000, description="Image height in pixels.", examples=[900])
    road_ids: bool = Field(default=True, description="Label each road with its OpenDRIVE id (`plot` only).")
    sampling_step: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description=(
            "Distance in metres between sampled points along each lane (`odrplot` sampling step). `plot` draws "
            "these points; `odrviewer` uses them only to frame the picture."
        ),
        examples=[1.0],
    )
