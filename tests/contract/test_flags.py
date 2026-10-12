"""Contract: every flag the service can emit exists in the --help text of the pinned esmini build (PLAN.md §6.4).

The help texts in docs/reference/ are captured from the image (`make help-texts`); the integration tests check
that they still match the binaries in the running container.
"""

import re
from pathlib import Path

import pytest

from app.esmini.args import (
    build_dat2csv_argv,
    build_esmini_argv,
    build_odrplot_argv,
    build_odrviewer_argv,
    build_replayer_argv,
)
from app.models.simulation import CameraMode, LogLevel, SimulationConfig, VideoOptions

REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "reference"
_OPTION = re.compile(r"^\s+\[?(--[A-Za-z][A-Za-z0-9_-]*)", re.MULTILINE)

# All flags the service may emit today. Adding a flag to app/esmini/args.py without listing it here fails
# test_emitted_flags_are_the_expected_ones, which forces a look at the help text.
EXPECTED_ESMINI_FLAGS = {
    "--headless",
    "--osc",
    "--fixed_timestep",
    "--record",
    "--disable_log",
    "--log_level",
    "--collision",
    "--disable_controllers",
    "--seed",
}

EXPECTED_REPLAYER_FLAGS = {
    "--file",
    "--res_path",
    "--headless",
    "--window",
    "--camera_mode",
    "--gui",
    "--hide_trajectories",
    "--capture_screen",
    "--quit_at_end",
    "--logfile_path",
    "--stop_time",
}

EXPECTED_ODRVIEWER_FLAGS = {
    "--odr",
    "--headless",
    "--window",
    "--density",
    "--custom_fixed_top_camera",
    "--capture_screen",
    "--duration",
    "--disable_log",
}


def documented_flags(tool: str) -> set[str]:
    path = REFERENCE / f"{tool}-help.txt"
    if not path.is_file():
        pytest.fail(f"{path} is missing; run `make help-texts`")
    return set(_OPTION.findall(path.read_text()))


def flags(argv: list[str]) -> set[str]:
    return {a for a in argv if a.startswith("--")}


def maximal_config() -> SimulationConfig:
    """A configuration that switches on every optional flag."""
    return SimulationConfig.model_validate(
        {"collisionDetection": True, "disableControllers": True, "seed": 1, "logLevel": "debug"}
    )


def test_help_text_parser_finds_known_options() -> None:
    documented = documented_flags("esmini")
    assert {"--osc", "--headless", "--window", "--capture_screen", "--param_dist"} <= documented
    assert "--screen_capture" not in documented  # misspelling in one user-guide example (PLAN.md F9)
    assert "--param" not in documented  # no parameter override option exists (PLAN.md F11)


def test_emitted_flags_are_the_expected_ones() -> None:
    argv = build_esmini_argv(Path("esmini"), "s.xosc", maximal_config(), Path("sim.dat"))
    assert flags(argv) == EXPECTED_ESMINI_FLAGS


def test_esmini_flags_exist_in_help_text() -> None:
    argv = build_esmini_argv(Path("esmini"), "s.xosc", maximal_config(), Path("sim.dat"))
    assert flags(argv) - documented_flags("esmini") == set()


def test_log_levels_match_help_text() -> None:
    help_text = (REFERENCE / "esmini-help.txt").read_text()
    documented = re.search(r"--log_level.*\n\s+Log level (.+)", help_text).group(1)
    assert [level.value for level in LogLevel] == [x.strip() for x in documented.split(",")]


def test_dat2csv_flags_exist_in_help_text() -> None:
    argv = build_dat2csv_argv(Path("dat2csv"), Path("sim.dat"), Path("sim.csv"))
    assert flags(argv) - documented_flags("dat2csv") == set()


def test_reference_matches_pinned_version() -> None:
    assert 'ESMINI_GIT_TAG="v3.8.1"' in (REFERENCE / "esmini-version.txt").read_text()


def replayer_argv() -> list[str]:
    return build_replayer_argv(Path("replayer"), Path("sim.dat"), Path("in"), VideoOptions(), 5.0, Path("log.txt"))


def test_replayer_flags_are_the_expected_ones_and_documented() -> None:
    assert flags(replayer_argv()) == EXPECTED_REPLAYER_FLAGS
    assert flags(replayer_argv()) - documented_flags("replayer") == set()


def test_camera_modes_match_help_text() -> None:
    help_text = (REFERENCE / "replayer-help.txt").read_text()
    documented = re.search(r"--camera_mode.*\n\s+Initial camera mode \(([^)]+)\)", help_text).group(1)
    modes = {m.strip().strip('"') for m in documented.split(",")}
    assert {m.value for m in CameraMode} == modes - {"custom"}  # custom needs camera positions


def test_odrplot_usage_matches_positional_arguments() -> None:
    usage = (REFERENCE / "odrplot-help.txt").read_text()
    assert "openDriveFile.xodr [Output file, default=output.csv] [Sampling_step, default=1.0]" in usage
    assert flags(build_odrplot_argv(Path("odrplot"), Path("r.xodr"), Path("r.csv"), 1.0)) == set()


def test_odrviewer_flags_are_the_expected_ones_and_documented() -> None:
    argv = build_odrviewer_argv(Path("odrviewer"), Path("r.xodr"), 800, 600, (0.0, 0.0, 1000.0, 1.57))
    assert flags(argv) == EXPECTED_ODRVIEWER_FLAGS
    assert flags(argv) - documented_flags("odrviewer") == set()
