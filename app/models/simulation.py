"""Configuration, job-state and result schemas of `POST /v1/simulations` (PLAN.md §6.3, §6.6)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from app.models.common import RequestModel, ResponseModel


class OutputKind(str, Enum):
    DAT = "dat"
    CSV = "csv"
    LOG = "log"
    VIDEO = "video"


class CameraMode(str, Enum):
    """Camera modes of esmini's viewer (`--camera_mode`), except `custom`, which needs camera positions."""

    ORBIT = "orbit"
    FIXED = "fixed"
    FLEX = "flex"
    FLEX_ORBIT = "flex-orbit"
    TOP = "top"
    DRIVER = "driver"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Limits(RequestModel):
    wall_timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Wall-clock limit for the esmini run in seconds. esmini has no simulated-time limit, so a scenario "
            "without a reachable stop trigger runs until this timeout. Defaults to the server's "
            "`defaultTimeoutSeconds`; must not exceed `maxTimeoutSeconds` (see `GET /v1/info`)."
        ),
        examples=[60],
    )


class VideoOptions(RequestModel):
    """Options of the video (`outputs` contains `video`). The recording of the run is replayed in esmini's 3D viewer
    off-screen and encoded as H.264 MP4. Vehicles without 3D models are drawn as boxes."""

    width: int = Field(default=1280, ge=160, le=1920, multiple_of=2, description="Frame width in pixels.")
    height: int = Field(default=720, ge=90, le=1080, multiple_of=2, description="Frame height in pixels.")
    frames_per_second: int = Field(
        default=20,
        ge=1,
        le=60,
        description=(
            "Frame rate of the video, which plays in real time. esmini renders one image per simulation step "
            "(`timestep`); images are dropped or repeated to reach this rate. Rendering time therefore grows with "
            "the number of steps: use a coarser `timestep` for long scenarios."
        ),
        examples=[20],
    )
    camera_mode: CameraMode = Field(
        default=CameraMode.ORBIT, description="Initial camera of the viewer (esmini `--camera_mode`)."
    )


class SimulationConfig(RequestModel):
    """Options of a simulation run. Every field maps to a fixed esmini argument; there is no pass-through."""

    timestep: float = Field(
        default=0.05,
        ge=0.001,
        le=1.0,
        description="Fixed simulation time step in seconds (esmini `--fixed_timestep`).",
        examples=[0.05],
    )
    collision_detection: bool = Field(
        default=True,
        description=(
            "Global collision detection (esmini `--collision`): collisions between entities are detected and logged. "
            "On by default; set `false` to save some time in scenarios with many entities."
        ),
    )
    disable_controllers: bool = Field(
        default=False, description="Disable all controllers defined in the scenario (esmini `--disable_controllers`)."
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2_147_483_647,
        description="Seed of esmini's random number generator (esmini `--seed`). Omit for esmini's default.",
        examples=[42],
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, description="esmini log level (esmini `--log_level`).")
    outputs: list[OutputKind] = Field(
        default_factory=lambda: [OutputKind.DAT, OutputKind.CSV, OutputKind.LOG],
        description=(
            "Artefacts to keep: `dat` (esmini recording `sim.dat`), `csv` (entity states per time step, "
            "`sim.csv`, produced by `dat2csv --extended`), `log` (esmini console log `log.txt`), `video` "
            "(`video.mp4`, the run rendered in esmini's 3D viewer; see `video`). Videos take much longer than "
            "the simulation itself and cover at most `maxVideoSeconds` of simulation time (see `GET /v1/info`)."
        ),
        examples=[["dat", "csv", "log", "video"]],
    )
    video: VideoOptions = Field(
        default_factory=VideoOptions, description="Options of the video; used only if `outputs` contains `video`."
    )
    limits: Limits = Field(default_factory=Limits)

    @field_validator("outputs")
    @classmethod
    def _unique_outputs(cls, value: list[OutputKind]) -> list[OutputKind]:
        if len(set(value)) != len(value):
            raise ValueError("outputs must not contain duplicates")
        return value


class JobState(str, Enum):
    """Where a job is in its life cycle: `queued -> running -> finished`, or `interrupted`."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"


class JobStatus(str, Enum):
    """Outcome of a finished job."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class JobStateResponse(ResponseModel):
    job_id: str = Field(examples=["q7c1hX0yD2mVb5fT8aLkQw"])
    state: JobState = Field(
        description=(
            "`queued`: waiting for a free esmini slot. `running`: esmini is running. `finished`: the result is "
            "available at `resultUrl`, whatever the outcome (see its `status`). `interrupted`: the service stopped "
            "before the job finished (e.g. the container was restarted); there is no result."
        )
    )
    submitted_at: datetime = Field(description="When the job was submitted (its files were stored).")
    status_url: str = Field(description="Relative URL of this status.", examples=["/v1/jobs/q7c1.../status"])
    result_url: str = Field(
        description="Relative URL of the result; it answers `409` until the job is finished.",
        examples=["/v1/jobs/q7c1..."],
    )


class Artifact(ResponseModel):
    name: str = Field(examples=["sim.csv"])
    size: int = Field(description="Size in bytes.")
    content_type: str = Field(examples=["text/csv"])
    url: str = Field(description="Relative download URL.", examples=["/v1/jobs/q7c1.../artifacts/sim.csv"])


class SimulationResult(ResponseModel):
    job_id: str
    status: JobStatus = Field(
        description=(
            "`succeeded`: esmini exited with code 0. `failed`: non-zero exit code or termination by a signal; "
            "see `warnings` and `log.txt`. `timed_out`: the wall-clock limit was reached and the run was killed."
        )
    )
    scenario: str = Field(description="File name of the scenario that was run.")
    esmini_version: str = Field(examples=["3.8.1"])
    exit_code: int | None = Field(description="esmini exit code; `null` if the process was killed on timeout.")
    wall_seconds: float = Field(description="Wall-clock duration of the esmini run.")
    simulated_seconds: float | None = Field(
        description="Simulation time of the last recorded step, taken from the CSV; `null` if unavailable."
    )
    created_at: datetime
    warnings: list[str] = Field(
        description="esmini log lines of level `warn` or `error` (deduplicated, truncated) and service notes."
    )
    artifacts: list[Artifact]
    config: SimulationConfig = Field(description="The effective configuration, including defaults.")
