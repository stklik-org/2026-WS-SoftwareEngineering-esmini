"""Config -> command line mapping (PLAN.md §6.4). Expected command lines are spelled out in full."""

from pathlib import Path

from app.esmini.args import build_dat2csv_argv, build_esmini_argv
from app.models.simulation import SimulationConfig

ESMINI = Path("/opt/esmini/bin/esmini")
RECORD = Path("/data/jobs/j/work/sim.dat")


def test_defaults() -> None:
    assert build_esmini_argv(ESMINI, "cut-in.xosc", SimulationConfig(), RECORD) == [
        "/opt/esmini/bin/esmini",
        "--headless",
        "--osc",
        "cut-in.xosc",
        "--fixed_timestep",
        "0.05",
        "--record",
        "/data/jobs/j/work/sim.dat",
        "--disable_log",
        "--log_level",
        "info",
        "--collision",  # on by default
    ]


def test_collision_detection_can_be_switched_off() -> None:
    config = SimulationConfig.model_validate({"collisionDetection": False})
    assert "--collision" not in build_esmini_argv(ESMINI, "cut-in.xosc", config, RECORD)


def test_all_options() -> None:
    config = SimulationConfig.model_validate(
        {"timestep": 0.01, "collisionDetection": True, "disableControllers": True, "seed": 42, "logLevel": "debug"}
    )
    assert build_esmini_argv(ESMINI, "my scenario.xosc", config, RECORD) == [
        "/opt/esmini/bin/esmini",
        "--headless",
        "--osc",
        "my scenario.xosc",  # one argv element: no shell is involved, spaces need no quoting
        "--fixed_timestep",
        "0.01",
        "--record",
        "/data/jobs/j/work/sim.dat",
        "--disable_log",
        "--log_level",
        "debug",
        "--collision",
        "--disable_controllers",
        "--seed",
        "42",
    ]


def test_dat2csv() -> None:
    assert build_dat2csv_argv(Path("/opt/esmini/bin/dat2csv"), Path("/w/sim.dat"), Path("/w/sim.csv")) == [
        "/opt/esmini/bin/dat2csv",
        "--file",
        "/w/sim.dat",
        "--extended",
        "--csv",
        "/w/sim.csv",
    ]
