import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import get_services
from app.core.errors import ApiError
from app.core.jobs import Job
from app.models.common import ErrorResponse
from app.models.simulation import Artifact, JobState, JobStateResponse, SimulationResult
from app.services.simulation import CONTENT_TYPES, Services, artifact_for, job_state

router = APIRouter(tags=["jobs"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Unknown or expired job, or unknown artefact."}}


def _job(services: Services, job_id: str) -> Job:
    job = services.jobs.get(job_id)
    if job is None:
        raise ApiError(404, "job_not_found", f"Job {job_id!r} does not exist or has expired.")
    return job


@router.get("/jobs", summary="List all jobs", response_model=list[JobStateResponse])
@router.get("/jobs/", include_in_schema=False)  # served directly: Java's HttpClient does not follow redirects
async def list_jobs(services: Annotated[Services, Depends(get_services)]) -> list[JobStateResponse]:
    """Every job the service still holds, whatever its state, newest submission first. Finished jobs are deleted
    `jobTtlSeconds` after they finished (see `GET /v1/info`)."""
    jobs = services.jobs
    return [job_state(job, jobs.state(job)) for job in await asyncio.to_thread(jobs.list_jobs)]


@router.get(
    "/jobs/{job_id}/status",
    summary="State of a job",
    response_model=JobStateResponse,
    responses=_NOT_FOUND,
)
async def get_job_status(job_id: str, services: Annotated[Services, Depends(get_services)]) -> JobStateResponse:
    """`queued`, `running`, `finished` or `interrupted`. Once `finished`, the result is available at `resultUrl`."""
    job = _job(services, job_id)
    return job_state(job, services.jobs.state(job))


@router.get(
    "/jobs/{job_id}",
    summary="Result of a job",
    response_model=SimulationResult,
    responses={
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "The job is still queued or running; poll its status."},
    },
)
async def get_job(job_id: str, services: Annotated[Services, Depends(get_services)]) -> SimulationResult:
    job = _job(services, job_id)
    state = services.jobs.state(job)
    if state == JobState.INTERRUPTED:
        raise ApiError(404, "job_not_found", f"Job {job_id!r} was interrupted and has no result.")
    result = job.read_result() if state == JobState.FINISHED else None
    if result is None:
        raise ApiError(
            409,
            "job_not_finished",
            f"Job {job_id!r} is {state.value}; poll /v1/jobs/{job_id}/status until it is finished.",
        )
    return result


@router.get(
    "/jobs/{job_id}/artifacts",
    summary="List the artefacts of a job",
    response_model=list[Artifact],
    responses=_NOT_FOUND,
)
async def list_artifacts(job_id: str, services: Annotated[Services, Depends(get_services)]) -> list[Artifact]:
    job = _job(services, job_id)
    return [artifact_for(job, p.name) for p in job.artifact_paths()]


@router.get(
    "/jobs/{job_id}/artifacts/{name}",
    summary="Download an artefact",
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}, "text/csv": {}, "text/plain": {}}}, **_NOT_FOUND},
)
async def download_artifact(
    job_id: str, name: str, services: Annotated[Services, Depends(get_services)]
) -> FileResponse:
    path = _job(services, job_id).artifact(name)
    if path is None:
        raise ApiError(404, "artifact_not_found", f"Job {job_id!r} has no artefact {name!r}.")
    return FileResponse(path, media_type=CONTENT_TYPES.get(path.suffix, "application/octet-stream"), filename=name)
