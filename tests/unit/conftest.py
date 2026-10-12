"""Unit-test fixtures: a fake esmini installation and fake Xvfb/ffmpeg, so that the API can be exercised without
Docker.

The fake `esmini` understands directives in XML comments of the scenario:
`fake:sleep=<seconds>` (to provoke a timeout), `fake:exit=<code>` (to provoke a failure). It writes its argv
next to the recording (`sim.argv.json`) so that tests can check which flags the service passed.

The fake `replayer` writes real TGA frames (headers as esmini's), one per recorded step of 12.5 s, and its argv to
`replayer.argv.json` next to its log; an uploaded `fail-replayer.txt` makes it exit with 3. It also logs the most
frames it saw on disk at once (`peak=<n>`). The fake `ffmpeg` counts the frames it reads. Both can be slowed down
per frame (`FAKE_REPLAYER_DELAY`, `FAKE_FFMPEG_DELAY`). The fake `Xvfb` reports display 42 through `-displayfd`.
The fake `odrplot` writes two lane borders; `fake:fail` in the road makes it exit with 1, `fake:empty` makes it
write no lanes. The fake `odrviewer` checks `DISPLAY` and writes two TGA frames of the window size, blue with a
red bottom row; `fake:viewer-fail` makes it exit with 1, `fake:viewer-blank` exit with 0 and no frames,
`fake:viewer-sleep` sleep for an hour.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app

FAKE_ESMINI = """\
import json, re, sys, time
from pathlib import Path
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
scenario, record = value("--osc"), value("--record")
print("esmini GIT TAG: v3.8.1 (fake)")
text = Path(scenario).read_text()
if record:
    Path(record).with_suffix(".argv.json").write_text(json.dumps(args))
print("[] [info] Loaded " + scenario)
print("[] [warn] fake warning")
print("[] [warn] fake warning")
m = re.search(r"fake:exit=(\\d+)", text)
if m:
    print("[] [error] fake failure", flush=True)
    sys.exit(int(m.group(1)))
m = re.search(r"fake:sleep=([\\d.]+)", text)
if m:
    sys.stdout.flush()
    time.sleep(float(m.group(1)))
if record:
    Path(record).write_text(f"FAKE-DAT 12.5 {value('--fixed_timestep')}\\n")
print("[12.500] [info] Closing")
"""

FAKE_DAT2CSV = """\
import sys
from pathlib import Path
args = sys.argv[1:]
dat, csv = Path(args[args.index("--file") + 1]), Path(args[args.index("--csv") + 1])
end = float(dat.read_text().split()[1])
rows = ["time, id, name, x, y"]
t = 0.0
while t <= end + 1e-9:
    rows.append(f"{t:.3f}, 0, Ego, {t * 10:.3f}, 0.000")
    t += 2.5
csv.write_text("\\n".join(rows) + "\\n")
"""


FAKE_REPLAYER = """\
import json, os, struct, sys, time
from pathlib import Path
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
delay = float(os.environ.get("FAKE_REPLAYER_DELAY", "0"))
log = Path(value("--logfile_path"))
log.with_name("replayer.argv.json").write_text(json.dumps(args))
if (Path(value("--res_path")) / "fail-replayer.txt").exists():
    sys.exit(3)
w, h = (int(v) for v in args[args.index("--window") + 3 : args.index("--window") + 5])
_, end, step = Path(value("--file")).read_text().split()
end, step = float(end), float(step)  # like esmini's replayer: one frame per recorded step
if value("--stop_time"):
    end = min(end, int(value("--stop_time")) / 1000)
header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 24, 0)
peak, i = 0, 0
while i * step <= end + 1e-9:
    Path(f"screen_shot_{i:05d}.tga").write_bytes(header + bytes(w * h * 3))
    peak = max(peak, len(list(Path(".").glob("screen_shot_*.tga"))))
    i += 1
    time.sleep(delay)
log.write_text(f"frames={i} peak={peak}\\n")
"""

FAKE_XVFB = """\
import os, sys, time
args = sys.argv[1:]
os.write(int(args[args.index("-displayfd") + 1]), b"42\\n")
time.sleep(3600)
"""

FAKE_FFMPEG = """\
import os, sys, time
from pathlib import Path
args = sys.argv[1:]
w, h = (int(v) for v in args[args.index("-s") + 1].split("x"))
delay = float(os.environ.get("FAKE_FFMPEG_DELAY", "0"))
frames = 0
while True:
    chunk = sys.stdin.buffer.read(w * h * 3)
    if not chunk:
        break
    frames += 1
    time.sleep(delay)
Path(args[-1]).write_text(f"FAKE-MP4 frames={frames} size={w}x{h}\\n")
"""

FAKE_ODRPLOT = """\
import sys
from pathlib import Path
road, csv = Path(sys.argv[1]), Path(sys.argv[2])
text = road.read_text()
print("[] [info] Loaded " + road.name)
if "fake:fail" in text:
    print("[] [error] fake odrplot failure")
    sys.exit(1)
rows = [] if "fake:empty" in text else [
    "lane, 0, 0, 0, none", "0.0, 0.0, 0.0, 0.0", "100.0, 0.0, 0.0, 0.0",
    "lane, 0, 0, -1, driving", "0.0, -3.5, 0.0, 0.0", "100.0, -3.5, 0.0, 0.0",
    "lane, 1, 0, 0, none", "100.0, 0.0, 0.0, 0.0", "150.0, 40.0, 0.0, 0.0",
]
csv.write_text("\\n".join(rows) + "\\n")
"""

FAKE_ODRVIEWER = """\
import os, struct, sys, time
from pathlib import Path
args = sys.argv[1:]
text = Path(args[args.index("--odr") + 1]).read_text()
if os.environ.get("DISPLAY") != ":42" or os.environ.get("LIBGL_ALWAYS_SOFTWARE") != "1":
    print("no display", flush=True)
    sys.exit(4)
if "fake:viewer-fail" in text:
    print("[] [error] fake odrviewer failure")
    sys.exit(1)
if "fake:viewer-sleep" in text:
    time.sleep(3600)
if "fake:viewer-blank" in text:
    sys.exit(0)
w, h = (int(v) for v in args[args.index("--window") + 3 : args.index("--window") + 5])
header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, w, h, 24, 0)
pixels = b"\\x00\\x00\\xff" * w + b"\\xff\\x00\\x00" * (w * (h - 1))  # BGR, bottom row first
for i in range(2):
    Path(f"screen_shot_{i:05d}.tga").write_bytes(header + pixels)
"""


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def fake_esmini_home(tmp_path: Path) -> Path:
    home = tmp_path / "esmini"
    (home / "bin").mkdir(parents=True)
    _script(home / "bin" / "esmini", FAKE_ESMINI)
    _script(home / "bin" / "dat2csv", FAKE_DAT2CSV)
    _script(home / "bin" / "replayer", FAKE_REPLAYER)
    _script(home / "bin" / "odrplot", FAKE_ODRPLOT)
    _script(home / "bin" / "odrviewer", FAKE_ODRVIEWER)
    (home / "version.txt").write_text(
        'ESMINI_GIT_REV="v3.8.1-0-19d26b68"\nESMINI_GIT_TAG="v3.8.1"\nESMINI_BUILD_VERSION="6383"\n'
    )
    return home


@pytest.fixture(autouse=True)
def fake_video_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Xvfb and ffmpeg fakes first on PATH, so that tests never use real ones that may be installed."""
    tools = tmp_path / "tools"
    tools.mkdir()
    _script(tools / "Xvfb", FAKE_XVFB)
    _script(tools / "ffmpeg", FAKE_FFMPEG)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ.get('PATH', '')}")
    return tools


@pytest.fixture
def settings(tmp_path: Path, fake_esmini_home: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        esmini_home=fake_esmini_home,
        image_version="test",
        max_concurrent_jobs=2,
        max_queued_jobs=1,
        max_upload_mb=0.5,
        max_total_upload_mb=1,
        default_timeout_s=10,
        max_timeout_s=20,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def scenario_bytes(data_dir: Path) -> bytes:
    return (data_dir / "cut-in.xosc").read_bytes()


@pytest.fixture
def road_bytes(data_dir: Path) -> bytes:
    return (data_dir / "e6mini.xodr").read_bytes()
