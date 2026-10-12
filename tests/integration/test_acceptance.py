"""Acceptance tests (PLAN.md §12.2), run against the real esmini in the container."""

from __future__ import annotations

import csv
import io
import json
import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from tests.conftest import DATA as DATA_DIR
from tests.integration.conftest import CUT_IN_FILES, Container, simulate, submit, wait_for

pytestmark = pytest.mark.integration

REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "reference"


def test_at1_health(client: httpx.Client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_at2_info(client: httpx.Client) -> None:
    body = client.get("/v1/info").json()
    assert body["esmini"]["version"] == "3.8.1"
    assert body["esmini"]["gitRevision"].startswith("v3.8.1-0-19d26b6")
    assert body["arch"] == "amd64"
    assert body["capabilities"]["simulations"] is True


def test_at3_simulation_with_uploaded_files(client: httpx.Client) -> None:
    response = submit(
        client, "cut-in.xosc", *CUT_IN_FILES, config='{"timestep": 0.05, "outputs": ["dat", "csv", "log"]}'
    )
    assert response.status_code == 202, response.text
    assert response.json()["state"] in ("queued", "running", "finished")
    result = wait_for(client, response.json()["jobId"])
    assert result["status"] == "succeeded", result
    assert result["exitCode"] == 0
    assert result["esminiVersion"] == "3.8.1"
    # cut-in.xosc stops 5 s after its cut-in manoeuvre ends; with the pinned esmini that is at t = 22 s.
    assert result["simulatedSeconds"] == pytest.approx(22.0, abs=0.06)
    downloads = {a["name"]: client.get(a["url"]) for a in result["artifacts"]}
    assert set(downloads) == {"sim.dat", "sim.csv", "log.txt"}
    assert all(r.status_code == 200 and r.content for r in downloads.values())

    rows = list(csv.DictReader(io.StringIO(downloads["sim.csv"].text), skipinitialspace=True))
    assert {"time", "id", "name", "x", "y", "h", "speed"} <= set(rows[0])
    assert {r["name"] for r in rows} == {"Ego", "OverTaker"}
    times = [float(r["time"]) for r in rows]
    assert times == sorted(times) and times[0] == 0.0

    log = downloads["log.txt"].text
    assert "Loaded OpenDRIVE: e6mini.xodr" in log  # the uploaded road, found by file name (PLAN.md F15)


@pytest.mark.parametrize(
    ("missing", "message"),
    [("e6mini.xodr", "e6mini.xodr"), ("VehicleCatalog.xosc", "Couldn't locate OpenSCENARIO file VehicleCatalog")],
)
def test_at4_files_are_not_taken_from_the_image(client: httpx.Client, missing: str, message: str) -> None:
    """The image has no esmini resources: a road or catalog that is not uploaded makes the run fail."""
    result = simulate(client, "cut-in.xosc", *(f for f in CUT_IN_FILES if f != missing))
    assert result["status"] == "failed", result
    assert result["exitCode"] == 255
    log = client.get(f"/v1/jobs/{result['jobId']}/artifacts/log.txt").text
    assert message in log


def test_at5_unknown_config_field(client: httpx.Client) -> None:
    response = submit(client, "cut-in.xosc", *CUT_IN_FILES, config='{"timestep": 0.05, "stopTime": 10}')
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["config", "stopTime"]


def test_results_are_reproducible(client: httpx.Client) -> None:
    """Same input on one architecture gives the same CSV (PLAN.md §12.3)."""
    csvs = []
    for _ in range(2):
        result = simulate(client, "cut-in.xosc", *CUT_IN_FILES, config='{"outputs": ["csv"]}')
        csvs.append(client.get(result["artifacts"][0]["url"]).content)
    assert csvs[0] == csvs[1]


def test_missing_road_fails_with_explanation(client: httpx.Client) -> None:
    result = simulate(client, "missing-road.xosc", *CUT_IN_FILES)
    assert result["status"] == "failed"
    assert result["exitCode"] == 255
    assert any("does-not-exist.xodr" in w for w in result["warnings"])


def test_status_reports_running_then_finished(client: httpx.Client) -> None:
    response = submit(client, "endless.xosc", *CUT_IN_FILES, config='{"limits": {"wallTimeoutSeconds": 3}}')
    assert response.status_code == 202, response.text
    job = response.json()
    assert response.headers["location"] == job["statusUrl"]
    deadline = time.monotonic() + 30
    while (state := client.get(job["statusUrl"]).json()["state"]) == "queued":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert state == "running"
    early = client.get(job["resultUrl"])
    assert early.status_code == 409
    assert early.json()["code"] == "job_not_finished"
    assert wait_for(client, job["jobId"])["status"] == "timed_out"
    assert client.get(job["statusUrl"]).json()["state"] == "finished"


def test_at10_endless_scenario_times_out(client: httpx.Client, request: pytest.FixtureRequest) -> None:
    result = simulate(client, "endless.xosc", *CUT_IN_FILES, config='{"limits": {"wallTimeoutSeconds": 5}}')
    assert result["status"] == "timed_out"
    assert result["exitCode"] is None
    assert 4.9 < result["wallSeconds"] < 9
    log = client.get(f"/v1/jobs/{result['jobId']}/artifacts/log.txt").text
    assert log.endswith("\n"), "the log of a killed run must not end in a partial line (stdbuf)"
    if "ESMINI_CONTAINER" in __import__("os").environ:
        container: Container = request.getfixturevalue("container")
        assert esmini_processes(container) == 0, "the timed-out esmini process must be gone"


def esmini_processes(container: Container) -> int:
    code = (
        "import os\n"
        "me = os.getpid()\n"
        "n = 0\n"
        "for p in filter(str.isdigit, os.listdir('/proc')):\n"
        "    if int(p) == me: continue\n"
        "    try: argv0 = open(f'/proc/{p}/cmdline', 'rb').read().split(b'\\0')[0]\n"
        "    except OSError: continue\n"
        "    n += argv0.endswith(b'/esmini')\n"
        "print(n)\n"
    )
    return int(container.python(code))


def test_at11_oversized_request_is_rejected_before_upload(base_url: str) -> None:
    """A declared 200 MB body (limit: 128 MB) is refused from the headers alone; the client never has to send it."""
    url = urlparse(base_url)
    with socket.create_connection((url.hostname, url.port or 80), timeout=10) as s:
        s.sendall(
            b"POST /v1/simulations HTTP/1.1\r\nHost: localhost\r\n"
            b"Content-Type: multipart/form-data; boundary=x\r\nContent-Length: 209715200\r\n"
            b"Expect: 100-continue\r\n\r\n"
        )
        status_line = s.recv(4096).split(b"\r\n", 1)[0]
    assert status_line.startswith(b"HTTP/1.1 413"), status_line


def test_at11_file_over_per_file_limit(client: httpx.Client) -> None:
    limit_mb = client.get("/v1/info").json()["limits"]["maxUploadMb"]
    padding = b" " * int((limit_mb + 1) * 1024 * 1024)
    content = b'<?xml version="1.0"?><OpenSCENARIO>' + padding + b"</OpenSCENARIO>"
    response = client.post(
        "/v1/simulations", files=[("files", ("big.xosc", content, "application/xml"))], data={"scenario": "big.xosc"}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd.xosc", "x.xosc/../../y.xosc", "..\\..\\evil.xosc", ".hidden.xosc", "a" * 300 + ".xosc"],
)
def test_at12_at20_unsafe_file_names(client: httpx.Client, name: str) -> None:
    content = (Path(__file__).resolve().parents[1] / "data" / "cut-in.xosc").read_bytes()
    response = client.post(
        "/v1/simulations", files=[("files", (name, content, "application/xml"))], data={"scenario": name}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_file_name"


def test_at20_esmini_installation_is_read_only(container: Container) -> None:
    result = container.exec("sh", "-c", "touch /opt/esmini/bin/x 2>&1; id -un")
    assert "Permission denied" in result.stdout
    assert result.stdout.strip().endswith("esmini"), "the service must not run as root"


def test_image_contains_no_esmini_resources(container: Container) -> None:
    result = container.exec("ls", "/opt/esmini")
    assert result.returncode == 0, result.stderr
    assert "resources" not in result.stdout.split(), result.stdout


def test_at14_concurrent_requests_are_admitted_or_refused(client: httpx.Client) -> None:
    limits = client.get("/v1/info").json()["limits"]
    capacity = limits["maxConcurrentJobs"] + limits["maxQueuedJobs"]
    if capacity > 8:
        pytest.skip(f"capacity {capacity} is too large for this test; `make test-integration` uses 2 + 2")
    requests = capacity + 3
    config = '{"limits": {"wallTimeoutSeconds": 2}}'
    with ThreadPoolExecutor(max_workers=requests) as pool:
        responses = list(
            pool.map(lambda _: submit(client, "endless.xosc", *CUT_IN_FILES, config=config), range(requests))
        )
    codes = sorted(r.status_code for r in responses)
    assert set(codes) <= {202, 429}, codes
    assert codes.count(429) >= 1, codes
    assert codes.count(202) >= limits["maxConcurrentJobs"], codes
    refused = next(r for r in responses if r.status_code == 429)
    assert refused.headers["retry-after"] == "5"
    assert refused.json()["code"] == "too_many_requests"
    assert client.get("/health").status_code == 200
    # Wait for the admitted jobs, so that later tests find free capacity.
    for accepted in (r for r in responses if r.status_code == 202):
        assert wait_for(client, accepted.json()["jobId"])["status"] == "timed_out"


def test_at21_port_is_published_on_loopback_only(container: Container) -> None:
    import subprocess

    ports = subprocess.run(["docker", "port", container.name, "8080"], capture_output=True, text=True).stdout.split()
    assert ports and all(p.startswith("127.0.0.1:") for p in ports), ports


def test_help_texts_match_the_binaries(container: Container) -> None:
    """The committed help texts (source of truth for flags) must describe the esmini inside this image."""
    for tool in ("esmini", "dat2csv"):
        live = container.exec(f"/opt/esmini/bin/{tool}", "--help")
        assert (live.stdout + live.stderr) == (REFERENCE / f"{tool}-help.txt").read_text(), tool


def test_at13_video_of_a_run(client: httpx.Client, request: pytest.FixtureRequest) -> None:
    config = '{"outputs": ["video", "log"], "video": {"width": 640, "height": 360, "framesPerSecond": 10}}'
    result = simulate(client, "cut-in.xosc", *CUT_IN_FILES, config=config)
    assert result["status"] == "succeeded", result
    assert not [w for w in result["warnings"] if w.startswith("no video")], result["warnings"]
    artifact = next(a for a in result["artifacts"] if a["name"] == "video.mp4")
    assert artifact["contentType"] == "video/mp4"
    mp4 = client.get(artifact["url"]).content
    assert mp4[4:8] == b"ftyp" and len(mp4) == artifact["size"] > 10_000
    if "ESMINI_CONTAINER" in __import__("os").environ:
        container: Container = request.getfixturevalue("container")
        probe = container.exec(
            "sh",
            "-c",
            f"ffprobe -v error -select_streams v:0 -count_frames -show_entries "
            f"stream=codec_name,width,height,r_frame_rate,nb_read_frames -of csv=p=0 "
            f"/data/jobs/{result['jobId']}/outputs/video.mp4",
        )
        codec, width, height, rate, frames = probe.stdout.strip().split(",")
        assert (codec, width, height, rate) == ("h264", "640", "360", "10/1")
        # 22 s of simulation at 10 frames per second: frames at 0.0, 0.1, ..., 22.0 s.
        assert abs(int(frames) - 221) <= 2, frames
        frames_dir = container.exec("ls", f"/data/jobs/{result['jobId']}/work/frames")
        assert "No such file" in frames_dir.stderr, "the frames must be deleted"


@pytest.mark.parametrize("renderer", ["odrviewer", "plot"])
def test_at9_road_preview(client: httpx.Client, renderer: str) -> None:
    from PIL import Image

    road = (Path(__file__).resolve().parents[1] / "data" / "e6mini.xodr").read_bytes()
    response = client.post(
        "/v1/road-previews",
        files=[("road", ("e6mini.xodr", road, "application/xml"))],
        data={"config": json.dumps({"renderer": renderer, "width": 800, "height": 600})},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    png = response.content
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (800, 600)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    background = image.getpixel((0, 0))
    drawn = sum(1 for pixel in image.getdata() if pixel != background)
    assert drawn > 0.01 * 800 * 600, "a drawn road network, not a blank image"


def test_media_capabilities(client: httpx.Client) -> None:
    capabilities = client.get("/v1/info").json()["capabilities"]
    assert capabilities["videos"] is True
    assert capabilities["roadPreviews"] is True


def test_uploaded_model_files_reach_esmini(client: httpx.Client) -> None:
    """Files of any type are stored next to the scenario: esmini looks up each vehicle's 3D model even when headless
    and warns if it is missing. With a file of that name uploaded the warning disappears (only its existence is
    checked here; no video is rendered from the placeholder)."""
    warning = "car_white.osgb not located"  # e.g. "3D model ../models/car_white.osgb not located, ..."
    without = simulate(client, "cut-in.xosc", *CUT_IN_FILES, config='{"outputs": ["log"]}')
    assert any(warning in w for w in without["warnings"]), without["warnings"]

    files = [("files", (n, (DATA_DIR / n).read_bytes(), "application/xml")) for n in ("cut-in.xosc", *CUT_IN_FILES)]
    files.append(("files", ("car_white.osgb", b"\x00placeholder model", "application/octet-stream")))
    response = client.post(
        "/v1/simulations", files=files, data={"scenario": "cut-in.xosc", "config": '{"outputs": ["log"]}'}
    )
    assert response.status_code == 202, response.text
    result = wait_for(client, response.json()["jobId"])
    assert result["status"] == "succeeded", result
    assert not any(warning in w for w in result["warnings"]), result["warnings"]


def test_list_jobs(client: httpx.Client) -> None:
    result = simulate(client, "cut-in.xosc", *CUT_IN_FILES, config='{"outputs": ["log"]}')
    for path in ("/v1/jobs", "/v1/jobs/"):
        response = client.get(path)
        assert response.status_code == 200, path
        listing = {j["jobId"]: j for j in response.json()}
        assert listing[result["jobId"]]["state"] == "finished", path
