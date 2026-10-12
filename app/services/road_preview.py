"""Road previews: an OpenDRIVE file to a PNG top view (PLAN.md FR-05).

`odrplot` samples every lane border into a CSV. The `odrviewer` renderer (Tier 2, the default) uses the samples to
place a top camera over the whole network and captures one frame of esmini's 3D viewer under a private Xvfb; the
`plot` renderer (Tier 1) draws the samples with matplotlib (`app.esmini.roadplot`) in a separate process. Every
step runs with a timeout in a temporary directory under `DATA_DIR/previews/`, which is deleted afterwards. Previews
are short and answered synchronously, without a job; `Services.previews` bounds how many run at once.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from app.core.errors import ApiError
from app.core.files import InputFile, validate_road_file
from app.esmini.args import build_odrplot_argv, build_odrviewer_argv
from app.esmini.outputs import log_warnings
from app.esmini.roadplot import read_borders
from app.esmini.roadview import fit_top_camera, last_frame, tga_to_png
from app.esmini.runner import run_process
from app.esmini.video import VideoError, xvfb_display
from app.models.road_preview import Renderer, RoadPreviewConfig
from app.services.simulation import Services

logger = logging.getLogger(__name__)


def previews_available(services: Services) -> bool:
    return services.installation.odrplot.is_file()


def viewer_available(services: Services) -> bool:
    """The `odrviewer` renderer also needs an X server to render into."""
    return services.installation.odrviewer.is_file() and shutil.which("Xvfb") is not None


async def render_road_preview(road: InputFile, config: RoadPreviewConfig, services: Services) -> bytes:
    settings, installation = services.settings, services.installation
    await asyncio.to_thread(validate_road_file, road, settings.max_upload_bytes)
    if not previews_available(services):
        raise ApiError(503, "esmini_unavailable", f"odrplot is not installed at {installation.odrplot}.")
    if config.renderer == Renderer.ODRVIEWER and not viewer_available(services):
        raise ApiError(501, "not_available", 'odrviewer or Xvfb is missing in this image; use "renderer": "plot".')

    async with services.previews.slot():
        work = settings.data_dir / "previews" / secrets.token_urlsafe(16)
        work.mkdir(parents=True)
        try:
            return await _render(work, road, config, services)
        finally:
            await asyncio.to_thread(shutil.rmtree, work, True)


async def _render(work: Path, road: InputFile, config: RoadPreviewConfig, services: Services) -> bytes:
    settings, installation = services.settings, services.installation
    (work / road.name).write_bytes(road.content)
    samples, png, log = work / "lanes.csv", work / "road.png", work / "log.txt"

    sampled = await run_process(
        build_odrplot_argv(installation.odrplot, work / road.name, samples, config.sampling_step),
        cwd=work,
        timeout=settings.preview_timeout_s,
        output=log,
        max_file_bytes=settings.max_output_bytes,
    )
    if sampled.timed_out:
        raise ApiError(504, "timeout", f"Sampling the road network took longer than {settings.preview_timeout_s:g} s.")
    if sampled.returncode != 0 or not samples.is_file():
        raise ApiError(
            422,
            "invalid_road",
            f"odrplot could not read {road.name!r} (exit code {sampled.returncode}).",
            [{"loc": ["road"], "message": w, "type": "invalid_road"} for w in log_warnings(log)[:5]],
        )
    if config.renderer == Renderer.ODRVIEWER:
        return await _view(work, work / road.name, samples, config, services)

    drawn = await run_process(
        [
            sys.executable,
            "-m",
            "app.esmini.roadplot",
            str(samples),
            str(png),
            str(config.width),
            str(config.height),
            "1" if config.road_ids else "0",
            road.name,
        ],
        cwd=Path(__file__).resolve().parents[2],  # the directory that contains the `app` package
        timeout=settings.preview_timeout_s,
        output=log,
        max_file_bytes=settings.max_output_bytes,
    )
    if drawn.timed_out:
        raise ApiError(504, "timeout", f"Drawing the road network took longer than {settings.preview_timeout_s:g} s.")
    if drawn.returncode != 0 or not png.is_file():
        logger.warning("Road preview of %s failed: %s\n%s", road.name, drawn, log.read_text(errors="replace")[-2000:])
        raise _nothing_to_draw(road.name)
    return png.read_bytes()


def _nothing_to_draw(name: str) -> ApiError:
    return ApiError(
        422,
        "invalid_road",
        f"The road network in {name!r} could not be drawn; it may contain no lanes.",
        [{"loc": ["road"], "message": "nothing to draw", "type": "invalid_road"}],
    )


async def _view(work: Path, road: Path, samples: Path, config: RoadPreviewConfig, services: Services) -> bytes:
    """One frame of odrviewer from a top camera that frames every sampled lane border."""
    settings = services.settings
    borders = await asyncio.to_thread(read_borders, samples)
    if not borders:
        raise _nothing_to_draw(road.name)
    camera = fit_top_camera(borders, config.width, config.height)
    frames, log = work / "frames", work / "log.txt"
    frames.mkdir()
    deadline = time.monotonic() + settings.preview_timeout_s
    try:
        async with xvfb_display(shutil.which("Xvfb") or "Xvfb", config.width, config.height, log, deadline) as display:
            viewed = await run_process(
                build_odrviewer_argv(services.installation.odrviewer, road, config.width, config.height, camera),
                cwd=frames,  # odrviewer writes its frames into the working directory
                timeout=max(0.1, deadline - time.monotonic()),
                output=log,
                max_file_bytes=settings.max_output_bytes,
                env={**os.environ, "DISPLAY": display, "LIBGL_ALWAYS_SOFTWARE": "1"},
            )
    except VideoError as exc:
        raise ApiError(500, "preview_failed", f"The road network could not be rendered: {exc}.") from None
    if viewed.timed_out:
        raise ApiError(504, "timeout", f"Rendering the road network took longer than {settings.preview_timeout_s:g} s.")
    frame = last_frame(frames)
    try:
        if viewed.returncode != 0 or frame is None:
            raise ValueError(f"odrviewer exited with code {viewed.returncode} and {'a' if frame else 'no'} frame")
        return await asyncio.to_thread(tga_to_png, frame.read_bytes(), config.width, config.height)
    except ValueError as exc:
        logger.warning("Road preview of %s failed: %s\n%s", road.name, exc, log.read_text(errors="replace")[-2000:])
        raise ApiError(
            500,
            "preview_failed",
            f'odrviewer could not render {road.name!r} ({exc}); "renderer": "plot" may still work.',
        ) from None
