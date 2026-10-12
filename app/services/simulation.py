"""Submits and runs simulation jobs. Transport-independent: the multipart endpoint (and later the JSON-inline
endpoint, FR-10) convert their input to `InputFile`s and call `submit_simulation`.

Submission validates the request, admits the job and stores its files, so that request errors still yield `4xx`.
The run itself happens in a background task, and its result is written to the job directory."""

from __future__ import annotations

import asyncio
import logging
import shutil
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import ApiError
from app.core.files import InputFile, validate_input_files
from app.core.jobs import Job, JobStore
from app.core.limiter import AdmissionLimiter
from app.core.settings import Settings
from app.esmini.args import build_dat2csv_argv, build_esmini_argv, build_replayer_argv
from app.esmini.installation import EsminiInstallation
from app.esmini.outputs import last_simulation_time, log_warnings
from app.esmini.runner import run_process
from app.esmini.video import VideoError, find_video_tools, render_video
from app.models.simulation import (
    Artifact,
    JobState,
    JobStateResponse,
    JobStatus,
    OutputKind,
    SimulationConfig,
    SimulationResult,
)

logger = logging.getLogger(__name__)

DAT2CSV_TIMEOUT_S = 120
CONTENT_TYPES = {
    ".dat": "application/octet-stream",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".mp4": "video/mp4",
}
ARTIFACT_NAMES = {
    OutputKind.DAT: "sim.dat",
    OutputKind.CSV: "sim.csv",
    OutputKind.LOG: "log.txt",
    OutputKind.VIDEO: "video.mp4",
}


@dataclass(frozen=True)
class Services:
    settings: Settings
    installation: EsminiInstallation
    jobs: JobStore
    previews: AdmissionLimiter


def effective_timeout(config: SimulationConfig, settings: Settings) -> float:
    timeout = config.limits.wall_timeout_seconds or settings.default_timeout_s
    if timeout > settings.max_timeout_s:
        raise ApiError(
            422,
            "validation_error",
            f"wallTimeoutSeconds must not exceed {settings.max_timeout_s:g}.",
            [
                {
                    "loc": ["config", "limits", "wallTimeoutSeconds"],
                    "message": f"must be <= {settings.max_timeout_s:g}",
                    "type": "less_than_equal",
                }
            ],
        )
    return timeout


def artifact_for(job: Job, name: str) -> Artifact:
    path = job.outputs / name
    return Artifact(
        name=name,
        size=path.stat().st_size,
        content_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
        url=f"/v1/jobs/{job.id}/artifacts/{name}",
    )


def _signal_note(returncode: int, settings: Settings) -> str:
    try:
        name = signal.Signals(-returncode).name
    except ValueError:
        name = f"signal {-returncode}"
    note = f"esmini was terminated by {name}"
    if -returncode == getattr(signal, "SIGXFSZ", -1):
        note += f" (an output file exceeded the limit of {settings.max_output_mb:g} MB)"
    return note


def videos_available(installation: EsminiInstallation) -> bool:
    return installation.replayer.is_file() and find_video_tools() is not None


def job_state(job: Job, state: JobState) -> JobStateResponse:
    return JobStateResponse(
        job_id=job.id,
        state=state,
        status_url=f"/v1/jobs/{job.id}/status",
        result_url=f"/v1/jobs/{job.id}",
        submitted_at=job.submitted_at or datetime.now(timezone.utc),
    )


async def submit_simulation(
    files: Sequence[InputFile], scenario: str, config: SimulationConfig, services: Services
) -> JobStateResponse:
    settings, installation, jobs = services.settings, services.installation, services.jobs
    timeout = effective_timeout(config, settings)
    await asyncio.to_thread(validate_input_files, files, scenario, settings.max_upload_bytes)
    if not installation.is_available():
        raise ApiError(503, "esmini_unavailable", f"esmini is not installed at {installation.home}.")
    if OutputKind.VIDEO in config.outputs and not videos_available(installation):
        raise ApiError(
            501,
            "not_available",
            "Videos are not available in this installation (replayer, Xvfb or ffmpeg is missing); "
            "see capabilities in GET /v1/info.",
            [{"loc": ["config", "outputs"], "message": "video is not available", "type": "not_available"}],
        )

    job = jobs.submit()
    created_at = datetime.now(timezone.utc)
    try:
        await asyncio.to_thread(_store_inputs, job, files)
    except BaseException:
        jobs.finish(job)
        raise
    jobs.start(_run(job, scenario, config, timeout, created_at, services))
    return job_state(job, JobState.QUEUED)


def _store_inputs(job: Job, files: Sequence[InputFile]) -> None:
    for f in files:
        (job.inputs / f.name).write_bytes(f.content)


async def _run(
    job: Job,
    scenario: str,
    config: SimulationConfig,
    timeout: float,
    created_at: datetime,
    services: Services,
) -> None:
    jobs = services.jobs
    try:
        async with jobs.slot(job):
            result = await _execute(job, scenario, config, timeout, created_at, services)
    except asyncio.CancelledError:  # shutdown: the job stays without a result
        jobs.finish(job)
        raise
    except Exception as exc:  # a bug in the service must not leave the job queued or running for ever
        logger.exception("Job %s failed in the service", job.id)
        result = SimulationResult(
            job_id=job.id,
            status=JobStatus.FAILED,
            scenario=scenario,
            esmini_version=services.installation.version.version,
            exit_code=None,
            wall_seconds=0.0,
            simulated_seconds=None,
            created_at=created_at,
            warnings=[f"internal error of the service ({type(exc).__name__}); see the service log"],
            artifacts=[],
            config=config,
        )
    try:
        await asyncio.to_thread(job.write_result, result)
    except Exception:
        logger.exception("Could not write the result of job %s", job.id)
    finally:
        jobs.finish(job)


async def _execute(
    job: Job,
    scenario: str,
    config: SimulationConfig,
    timeout: float,
    created_at: datetime,
    services: Services,
) -> SimulationResult:
    settings, installation = services.settings, services.installation

    dat, csv, log = job.work / "sim.dat", job.work / "sim.csv", job.work / "log.txt"
    # esmini runs in the directory of the uploaded files, so that references such as "../xodr/road.xodr" resolve
    # by file name there (PLAN.md F15). The image has no bundled resources to fall back to.
    run = await run_process(
        build_esmini_argv(installation.esmini, scenario, config, dat),
        cwd=job.inputs,
        timeout=timeout,
        output=log,
        max_file_bytes=settings.max_output_bytes,
        line_buffered=True,
    )

    notes: list[str] = []
    if run.timed_out:
        status = JobStatus.TIMED_OUT
        notes.append(
            f"esmini did not finish within {timeout:g} s and was killed; a recording (sim.dat) of a killed run is "
            "incomplete. Check that the scenario has a reachable StopTrigger."
        )
    elif run.returncode == 0:
        status = JobStatus.SUCCEEDED
    else:
        status = JobStatus.FAILED
        if run.returncode is not None and run.returncode < 0:
            notes.append(_signal_note(run.returncode, settings))
        else:
            notes.append(f"esmini exited with code {run.returncode}")

    # A partial recording of a timed-out run can be very long; converting it is not worth the wait.
    if not run.timed_out and dat.is_file() and dat.stat().st_size > 0:
        conversion = await run_process(
            build_dat2csv_argv(installation.dat2csv, dat, csv),
            cwd=job.work,
            timeout=DAT2CSV_TIMEOUT_S,
            output=job.work / "dat2csv.txt",
        )
        if conversion.returncode != 0 or not csv.is_file():
            notes.append("converting the recording to CSV failed; see the service log")
            logger.warning("dat2csv failed for job %s: %s", job.id, conversion)

    simulated = last_simulation_time(csv)
    video = job.work / "video.mp4"
    if OutputKind.VIDEO in config.outputs:
        note = await _render_video(job, dat, simulated, run.timed_out, config, video, services)
        if note:
            notes.append(note)
    esmini_warnings = log_warnings(log)
    produced = {OutputKind.DAT: dat, OutputKind.CSV: csv, OutputKind.LOG: log, OutputKind.VIDEO: video}
    artifacts = []
    for kind in config.outputs:
        source = produced[kind]
        if source.is_file():
            shutil.move(source, job.outputs / ARTIFACT_NAMES[kind])
            artifacts.append(artifact_for(job, ARTIFACT_NAMES[kind]))

    result = SimulationResult(
        job_id=job.id,
        status=status,
        scenario=scenario,
        esmini_version=installation.version.version,
        exit_code=run.returncode,
        wall_seconds=round(run.wall_seconds, 3),
        simulated_seconds=simulated,
        created_at=created_at,
        warnings=[*notes, *esmini_warnings],
        artifacts=artifacts,
        config=config,
    )
    return result


async def _render_video(
    job: Job,
    dat: Path,
    simulated: float | None,
    timed_out: bool,
    config: SimulationConfig,
    output: Path,
    services: Services,
) -> str | None:
    """Render the recording into `output`; returns a note for the result's warnings, if any."""
    settings, installation = services.settings, services.installation
    if timed_out:
        return "no video: the run timed out, so its recording is incomplete"
    if not dat.is_file() or dat.stat().st_size == 0:
        return "no video: esmini wrote no recording"
    tools = find_video_tools()
    if tools is None:  # checked at submission; only a changed installation gets here
        return "no video: Xvfb or ffmpeg is not installed"

    limit = settings.max_video_seconds
    stop = None if simulated is not None and simulated <= limit else limit
    note = None
    if simulated is not None and simulated > limit:
        note = f"the video covers only the first {limit:g} s of {simulated:g} s of simulation time (maxVideoSeconds)"
    frames = job.work / "frames"
    argv = build_replayer_argv(
        installation.replayer, dat, job.inputs, config.video, stop, job.work / "replayer_log.txt"
    )
    try:
        rendered = await render_video(
            argv,
            tools=tools,
            video=config.video,
            frames_dir=frames,
            output=output,
            log=job.work / "video.txt",
            step_seconds=config.timestep,
            timeout=settings.video_timeout_s,
            max_file_bytes=settings.max_output_bytes,
        )
    except VideoError as exc:
        output.unlink(missing_ok=True)
        logger.warning("Video of job %s failed: %s", job.id, exc)
        return f"no video: {exc}"
    finally:
        await asyncio.to_thread(shutil.rmtree, frames, True)
    logger.info("Job %s: video of %d frames in %.1f s", job.id, rendered.frames, rendered.wall_seconds)
    return note
