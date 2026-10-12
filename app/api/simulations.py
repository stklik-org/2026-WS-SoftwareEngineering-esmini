from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.deps import get_services
from app.core.errors import ApiError, validation_details
from app.core.files import InputFile
from app.models.common import ErrorResponse
from app.models.simulation import JobStateResponse, SimulationConfig
from app.services.simulation import Services, submit_simulation

router = APIRouter(tags=["simulations"])

BODY_SCHEMA = "Body_create_simulation_v1_simulations_post"  # name FastAPI derives from the endpoint
MAX_CONFIG_BYTES = 64 * 1024
# Swagger UI's "Try it out" pre-fills a list of files with one item whose value is the schema sample "string"; it is
# sent as a text field unless the user adds a file. Such placeholders (and empty values) carry no file.
FILE_PLACEHOLDERS = frozenset({"", "string"})
CONFIG_EXAMPLE = '{"timestep": 0.05, "outputs": ["dat", "csv", "log"], "limits": {"wallTimeoutSeconds": 60}}'

_ERRORS = {
    413: {"model": ErrorResponse, "description": "An uploaded file or the whole request is too large."},
    415: {
        "model": ErrorResponse,
        "description": "The scenario is not a `.xosc` file.",
    },
    422: {
        "model": ErrorResponse,
        "description": "Invalid configuration, file name or XML content, or the scenario is not among the files.",
    },
    429: {"model": ErrorResponse, "description": "Too many jobs are running or waiting; see `Retry-After`."},
}


async def read_config(part: str | StarletteUploadFile | None) -> str | None:
    """The config may arrive as a text field (`-F 'config=<file.json'`) or as a file part (`-F config=@file.json`)."""
    if not isinstance(part, StarletteUploadFile):
        return part
    raw = await part.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ApiError(413, "payload_too_large", f"The config part exceeds {MAX_CONFIG_BYTES // 1024} KB.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ApiError(
            422,
            "validation_error",
            "The config part is not UTF-8 text.",
            [{"loc": ["config"], "message": "not UTF-8 text", "type": "json_invalid"}],
        ) from None


def parse_config(raw: str | None) -> SimulationConfig:
    if raw is None or not raw.strip():
        return SimulationConfig()
    try:
        return SimulationConfig.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(
            422,
            "validation_error",
            "The config part is not a valid SimulationConfig.",
            validation_details(exc.errors(), prefix=["config"]),
        ) from None


async def _read(upload: UploadFile, max_bytes: int) -> InputFile:
    if upload.size is not None and upload.size > max_bytes:
        raise ApiError(
            413,
            "payload_too_large",
            f"{upload.filename!r} exceeds the per-file limit of {max_bytes / (1024 * 1024):g} MB.",
        )
    return InputFile(name=upload.filename or "", content=await upload.read())


@router.post(
    "/simulations",
    summary="Submit a scenario run",
    status_code=202,
    response_model=JobStateResponse,
    responses=_ERRORS,
)
async def create_simulation(
    services: Annotated[Services, Depends(get_services)],
    response: Response,
    files: Annotated[
        list[UploadFile | str],
        File(
            description=(
                "The scenario and every file it refers to: road networks (`.xodr`), catalogs (`.xosc`), SUMO files, "
                "3D models (`.osgb`), textures and any other data. Any file type is accepted; XML files are "
                "checked. The image contains no esmini sample files or models to fall back on."
            )
        ),
    ],
    scenario: Annotated[
        str,
        Form(description="File name of the scenario to run; must be one of `files` and end in `.xosc`."),
    ],
    config: Annotated[
        str | UploadFile | None,
        Form(
            description=(
                "Configuration as JSON text conforming to the `SimulationConfig` schema (see Schemas below). "
                "Omit for defaults. "
                "With curl: `-F 'config={\"timestep\": 0.05}'` or `-F 'config=<config.json'`."
            ),
            examples=[CONFIG_EXAMPLE],
        ),
    ] = None,
) -> JobStateResponse:
    """Checks the request, queues the run and answers at once with `202` and the job's URLs.

    All files are stored flat in one directory, which is esmini's working directory, so a reference such as
    `../xodr/e6mini.xodr` resolves to the uploaded `e6mini.xodr` by file name. A file that is referred to but not
    uploaded makes the run fail.

    Poll `statusUrl` until `state` is `finished`, then fetch the result from `resultUrl`. It lists the artefacts
    (`sim.dat`, `sim.csv`, `log.txt`), which can be downloaded until the job expires. A scenario that fails or
    times out still finishes, with result `status` `failed` or `timed_out`; its `warnings` and `log.txt` explain
    why. Errors in the request itself yield `4xx` with an error object, and no job is created.
    """
    cfg = parse_config(await read_config(config))
    limit = services.settings.max_upload_bytes
    inputs = []
    for item in files:
        if isinstance(item, StarletteUploadFile):
            inputs.append(await _read(item, limit))
        elif item.strip() not in FILE_PLACEHOLDERS:
            raise ApiError(
                422,
                "validation_error",
                "Every files part must be a file upload, not a text value.",
                [{"loc": ["body", "files"], "message": "expected a file", "type": "value_error"}],
            )
    job = await submit_simulation(inputs, scenario, cfg, services)
    response.headers["Location"] = job.status_url
    return job
