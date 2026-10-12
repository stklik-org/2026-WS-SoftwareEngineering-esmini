"""Application factory. Started by `python -m app` (uvicorn factory mode)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app import __version__
from app.api import health, info, jobs, road_previews, simulations
from app.core.errors import install_error_handlers
from app.core.jobs import JobStore
from app.core.limiter import AdmissionLimiter
from app.core.middleware import BodySizeLimitMiddleware
from app.core.settings import Settings
from app.esmini.installation import EsminiInstallation
from app.models.road_preview import RoadPreviewConfig
from app.models.simulation import SimulationConfig
from app.services.simulation import Services

DESCRIPTION = """
Runs [esmini](https://github.com/esmini/esmini) OpenSCENARIO simulations inside a local Docker container.

**Typical use:** `POST /v1/simulations` with all `files` the scenario needs (the scenario `.xosc`, its road
`.xodr`, catalogs, ...), the file name of the `scenario` among them, and a JSON `config`. The service answers `202`
at once. Poll `GET /v1/jobs/{jobId}/status` until `state` is `finished`, then fetch the result from
`GET /v1/jobs/{jobId}`. It lists artefacts (recording, CSV of entity states, log) that can be downloaded from
`/v1/jobs/{jobId}/artifacts/{name}` until the job expires. Add `video` to `config.outputs` for an MP4 of the run
in esmini's 3D viewer. The image contains no esmini sample scenarios, roads, catalogs or models: a run uses only
the uploaded files.

`POST /v1/road-previews` draws an OpenDRIVE road network as a PNG top view.

Errors are returned as `{"code": ..., "message": ..., "details": [...]}`. `GET /v1/info` reports the esmini
version, the features of this image and the server limits.

esmini is licensed under the Mozilla Public License 2.0.
"""

# Browsers and tools on the student's own machine; override with CORS_ORIGINS (comma-separated).
LOCAL_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    services = Services(
        settings=settings,
        installation=EsminiInstallation(settings.esmini_home),
        jobs=JobStore(
            settings.jobs_dir,
            max_concurrent=settings.max_concurrent_jobs,
            max_queued=settings.max_queued_jobs,
            ttl_seconds=settings.job_ttl_seconds,
        ),
        previews=AdmissionLimiter(settings.max_concurrent_jobs, settings.max_queued_jobs),
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.jobs.ensure_root()
        cleanup = asyncio.create_task(services.jobs.run_cleanup_loop())
        try:
            yield
        finally:
            cleanup.cancel()
            await services.jobs.cancel_all()

    app = FastAPI(
        title="esmini REST service",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.services = services
    install_error_handlers(app)

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_total_upload_bytes)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"]
        )
    else:
        app.add_middleware(
            CORSMiddleware, allow_origin_regex=LOCAL_ORIGIN_REGEX, allow_methods=["*"], allow_headers=["*"]
        )

    app.include_router(health.router)
    app.include_router(info.router, prefix="/v1")
    app.include_router(simulations.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(road_previews.router, prefix="/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    _tidy_multipart_schema(app)
    return app


def _tidy_multipart_schema(app: FastAPI) -> None:
    """Describe the multipart parts in the plain shapes that Swagger UI renders as usable form fields.

    FastAPI documents the list of files as `anyOf [file, string]` items. Swagger UI then shows text boxes instead of
    file pickers, and it renders any `contentMediaType` (e.g. application/json) as a file picker. So: `files` is a
    list of files and `config` a text field with an example.

    The `config` parts are JSON text that the endpoints parse themselves, so FastAPI does not know their models and
    would list them under "Schemas" only by accident (`SimulationConfig` is echoed in results, `RoadPreviewConfig`
    nowhere). Both are added explicitly, so that the descriptions that name them can be followed.
    """
    generate = app.openapi

    def openapi() -> dict:
        schema = generate()
        keep = ("title", "description", "examples")
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (SimulationConfig, RoadPreviewConfig):
            model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, definition in model_schema.pop("$defs", {}).items():
                components.setdefault(name, definition)
            components.setdefault(model.__name__, model_schema)
        body = schema.get("components", {}).get("schemas", {}).get(simulations.BODY_SCHEMA)
        preview = schema.get("components", {}).get("schemas", {}).get(road_previews.BODY_SCHEMA)
        if preview:
            config = preview["properties"]["config"]
            preview["properties"]["config"] = {"type": "string", **{k: config[k] for k in keep if k in config}}
        if body:  # idempotent: the schema is generated once and cached by FastAPI
            props = body["properties"]
            props["files"] = {
                "type": "array",
                # Swagger UI renders file pickers for list items only with the OpenAPI 3.0 spelling `format: binary`;
                # with `contentMediaType` it shows text boxes pre-filled with random sample bytes.
                "items": {"type": "string", "format": "binary"},
                **{k: props["files"][k] for k in keep if k in props["files"]},
            }
            props["config"] = {"type": "string", **{k: props["config"][k] for k in keep if k in props["config"]}}
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
