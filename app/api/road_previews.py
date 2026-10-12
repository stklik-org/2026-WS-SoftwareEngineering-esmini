from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from app.api.deps import get_services
from app.api.simulations import read_config
from app.core.errors import ApiError, validation_details
from app.core.files import InputFile
from app.models.common import ErrorResponse
from app.models.road_preview import RoadPreviewConfig
from app.services.road_preview import render_road_preview
from app.services.simulation import Services

router = APIRouter(tags=["road previews"])

BODY_SCHEMA = "Body_create_road_preview_v1_road_previews_post"  # name FastAPI derives from the endpoint
CONFIG_EXAMPLE = '{"renderer": "odrviewer", "width": 1200, "height": 900}'


def parse_config(raw: str | None) -> RoadPreviewConfig:
    if raw is None or not raw.strip():
        return RoadPreviewConfig()
    try:
        return RoadPreviewConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(
            422,
            "validation_error",
            "The config part is not a valid RoadPreviewConfig.",
            validation_details(exc.errors(), prefix=["config"]),
        ) from None


@router.post(
    "/road-previews",
    summary="Draw a road network as an image",
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "The road network as PNG."},
        413: {"model": ErrorResponse, "description": "The file or the whole request is too large."},
        415: {"model": ErrorResponse, "description": "The road is not an `.xodr` file."},
        422: {"model": ErrorResponse, "description": "Invalid configuration, file name or XML, or no drawable lanes."},
        429: {"model": ErrorResponse, "description": "Too many previews are running or waiting; see `Retry-After`."},
        500: {"model": ErrorResponse, "description": "odrviewer could not render the road network; try `plot`."},
        501: {"model": ErrorResponse, "description": "This image cannot run odrviewer; use `plot`."},
        504: {"model": ErrorResponse, "description": "Sampling or drawing took longer than the server allows."},
    },
)
async def create_road_preview(
    services: Annotated[Services, Depends(get_services)],
    road: Annotated[UploadFile, File(description="OpenDRIVE file (`.xodr`) of the road network.")],
    config: Annotated[
        str | UploadFile | None,
        Form(
            description=(
                "Options as JSON text conforming to the `RoadPreviewConfig` schema (see Schemas below). "
                "Omit for defaults."
            ),
            examples=[CONFIG_EXAMPLE],
        ),
    ] = None,
) -> Response:
    """Returns a top view of the whole road network as PNG, at once (no job).

    `renderer` chooses the picture. `odrviewer` (default) shows the network in esmini's 3D viewer, straight from
    above: road surfaces, markings and road objects, in flat colours (the image has no textures), x to the right and
    y up. `plot` draws every lane border (reference lines red, driving lanes blue, other lanes grey) with axes in
    metres and each road labelled with its id.
    """
    cfg = parse_config(await read_config(config))
    limit = services.settings.max_upload_bytes
    if road.size is not None and road.size > limit:
        raise ApiError(
            413, "payload_too_large", f"{road.filename!r} exceeds the per-file limit of {limit / (1024 * 1024):g} MB."
        )
    png = await render_road_preview(InputFile(name=road.filename or "", content=await road.read()), cfg, services)
    stem = (road.filename or "road").rsplit(".", 1)[0]
    return Response(png, media_type="image/png", headers={"Content-Disposition": f'inline; filename="{stem}.png"'})
