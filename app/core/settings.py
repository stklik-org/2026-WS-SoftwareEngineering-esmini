"""Service configuration, read once from environment variables (PLAN.md §6.7)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

_MB = 1024 * 1024


def _number(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be a number, got {raw!r}") from None
    if value < minimum:
        raise ValueError(f"Environment variable {name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    # Inside the container the server must listen on all interfaces so that Docker's port mapping works.
    # Exposure is controlled by the documented `-p 127.0.0.1:8080:8080` (PLAN.md FR-14, D8).
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080
    data_dir: Path = Path("/data")
    esmini_home: Path = Path("/opt/esmini")
    image_version: str = "dev"
    log_level: str = "info"
    max_concurrent_jobs: int = field(default_factory=lambda: os.cpu_count() or 2)
    max_queued_jobs: int = 16
    job_ttl_seconds: float = 3600
    max_upload_mb: float = 64  # environment models (.osgb) reach about 30 MB
    max_total_upload_mb: float = 128
    default_timeout_s: float = 60
    max_timeout_s: float = 300
    max_output_mb: float = 500
    video_timeout_s: float = 300
    max_video_seconds: float = 120
    preview_timeout_s: float = 60
    cors_origins: tuple[str, ...] = ()

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * _MB)

    @property
    def max_total_upload_bytes(self) -> int:
        return int(self.max_total_upload_mb * _MB)

    @property
    def max_output_bytes(self) -> int:
        return int(self.max_output_mb * _MB)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        defaults = cls()
        settings = cls(
            host=env.get("HOST", defaults.host),
            port=int(_number(env, "PORT", defaults.port, minimum=1)),
            data_dir=Path(env.get("DATA_DIR", str(defaults.data_dir))),
            esmini_home=Path(env.get("ESMINI_HOME", str(defaults.esmini_home))),
            image_version=env.get("IMAGE_VERSION", defaults.image_version),
            log_level=env.get("LOG_LEVEL", defaults.log_level).lower(),
            max_concurrent_jobs=int(_number(env, "MAX_CONCURRENT_JOBS", defaults.max_concurrent_jobs, minimum=1)),
            max_queued_jobs=int(_number(env, "MAX_QUEUED_JOBS", defaults.max_queued_jobs)),
            job_ttl_seconds=_number(env, "JOB_TTL_SECONDS", defaults.job_ttl_seconds, minimum=1),
            max_upload_mb=_number(env, "MAX_UPLOAD_MB", defaults.max_upload_mb, minimum=0.001),
            max_total_upload_mb=_number(env, "MAX_TOTAL_UPLOAD_MB", defaults.max_total_upload_mb, minimum=0.001),
            default_timeout_s=_number(env, "DEFAULT_TIMEOUT_S", defaults.default_timeout_s, minimum=0.1),
            max_timeout_s=_number(env, "MAX_TIMEOUT_S", defaults.max_timeout_s, minimum=0.1),
            max_output_mb=_number(env, "MAX_OUTPUT_MB", defaults.max_output_mb, minimum=1),
            video_timeout_s=_number(env, "VIDEO_TIMEOUT_S", defaults.video_timeout_s, minimum=1),
            max_video_seconds=_number(env, "MAX_VIDEO_SECONDS", defaults.max_video_seconds, minimum=1),
            preview_timeout_s=_number(env, "PREVIEW_TIMEOUT_S", defaults.preview_timeout_s, minimum=1),
            cors_origins=tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()),
        )
        if settings.default_timeout_s > settings.max_timeout_s:
            raise ValueError("DEFAULT_TIMEOUT_S must not exceed MAX_TIMEOUT_S")
        if settings.log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
            raise ValueError(f"LOG_LEVEL {settings.log_level!r} is not a valid uvicorn log level")
        return settings
