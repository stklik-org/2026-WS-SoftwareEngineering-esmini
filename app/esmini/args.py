"""Whitelisted mapping from configuration objects to esmini command lines (PLAN.md §6.4).

The service never forwards free-form arguments. Every flag emitted here must occur in the `--help` text of the
pinned esmini build that is committed in `docs/reference/`; `tests/contract/test_flags.py` enforces this.
"""

from __future__ import annotations

from pathlib import Path

from app.models.simulation import SimulationConfig, VideoOptions


def _number(value: float) -> str:
    return repr(float(value))


def build_esmini_argv(esmini: Path, scenario: str, config: SimulationConfig, record: Path) -> list[str]:
    """Command line of a headless run.

    `--disable_log` suppresses esmini's own log file (by default `log.txt` in the working directory, which is the
    directory of the uploaded files); the service captures stdout and stderr instead, which contain the same
    messages plus anything printed before the logger is initialised.
    """
    argv = [
        str(esmini),
        "--headless",
        "--osc",
        scenario,
        "--fixed_timestep",
        _number(config.timestep),
        "--record",
        str(record),
        "--disable_log",
        "--log_level",
        config.log_level.value,
    ]
    if config.collision_detection:
        argv.append("--collision")
    if config.disable_controllers:
        argv.append("--disable_controllers")
    if config.seed is not None:
        argv += ["--seed", str(config.seed)]
    return argv


def build_dat2csv_argv(dat2csv: Path, dat: Path, csv: Path) -> list[str]:
    """Convert a recording into one CSV row per entity and time step (`time, id, name, x, y, z, h, ...`)."""
    return [str(dat2csv), "--file", str(dat), "--extended", "--csv", str(csv)]


def build_replayer_argv(
    replayer: Path, dat: Path, res_path: Path, video: VideoOptions, stop_seconds: float | None, log: Path
) -> list[str]:
    """Replay a recording off-screen, writing one TGA frame per recorded time step (`screen_shot_00000.tga`, ... in
    the working directory); `app.esmini.video` resamples them to `framesPerSecond`. Needs an X display (Xvfb).
    The replayer's `--fixed_timestep` is not used: it has no effect when frames are captured.

    `--headless` must come before `--window`: that order gives an off-screen window that can capture images
    (PLAN.md F6). `--res_path` is the directory of the uploaded files, where the road named in the recording is.
    """
    argv = [
        str(replayer),
        "--file",
        str(dat),
        "--res_path",
        str(res_path),
        "--headless",
        "--window",
        "0",
        "0",
        str(video.width),
        str(video.height),
        "--camera_mode",
        video.camera_mode.value,
        "--gui",
        "off",
        "--hide_trajectories",
        "--capture_screen",
        "--quit_at_end",
        "--logfile_path",
        str(log),
    ]
    if stop_seconds is not None:
        argv += ["--stop_time", str(round(stop_seconds * 1000))]  # milliseconds
    return argv


def build_odrplot_argv(odrplot: Path, road: Path, csv: Path, sampling_step: float) -> list[str]:
    """Sample every lane border of a road network into a CSV (usage in docs/reference/odrplot-help.txt)."""
    return [str(odrplot), str(road), str(csv), _number(sampling_step)]


def build_odrviewer_argv(
    odrviewer: Path, road: Path, width: int, height: int, camera: tuple[float, float, float, float]
) -> list[str]:
    """Show a road network off-screen from a fixed top camera and capture it (`screen_shot_00000.tga`, ... in the
    working directory). Needs an X display (Xvfb). `camera` is `x, y, z, rotation` of
    `--custom_fixed_top_camera`; `app.esmini.roadview` fits it to the road network.

    `--headless` before `--window` gives an off-screen window that can capture images (PLAN.md F6). `--density 0`
    adds no cars. `--duration` is wall-clock time from the first frame: 0.5 s gives two or three identical frames
    (docs/spikes/02-rendering.md).
    """
    return [
        str(odrviewer),
        "--odr",
        str(road),
        "--headless",
        "--window",
        "0",
        "0",
        str(width),
        str(height),
        "--density",
        "0",
        "--custom_fixed_top_camera",
        ",".join(_number(v) for v in camera),
        "--capture_screen",
        "--duration",
        "0.5",
        "--disable_log",
    ]
