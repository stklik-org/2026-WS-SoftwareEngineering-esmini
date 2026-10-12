"""Videos and road previews with fake tools (see conftest.py). The real ones are exercised by tests/integration."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.esmini import roadplot, roadview, video
from app.esmini.args import build_odrviewer_argv
from app.esmini.video import VideoError, tga_pixels
from app.main import create_app
from app.models.simulation import VideoOptions
from app.services import road_preview, simulation
from tests.unit.helpers import scenario_with
from tests.unit.test_api import run, submit

SMALL_VIDEO = {"width": 160, "height": 90, "framesPerSecond": 10, "cameraMode": "top"}


def video_config(**overrides) -> str:
    return json.dumps({"outputs": ["log", "video"], "video": {**SMALL_VIDEO, **overrides}})


def work_file(settings: Settings, job_id: str, name: str) -> Path:
    return settings.jobs_dir / job_id / "work" / name


# --- videos ------------------------------------------------------------------------------------------------------


def test_video_is_rendered_streamed_and_listed(client: TestClient, scenario_bytes: bytes, settings: Settings) -> None:
    body = run(client, scenario_bytes, config=video_config())
    assert body["status"] == "succeeded"
    assert body["warnings"] == ["[warn] fake warning"]
    artifact = next(a for a in body["artifacts"] if a["name"] == "video.mp4")
    assert artifact["contentType"] == "video/mp4"
    # The fake recording lasts 12.5 s in steps of 0.05 s (251 images); at 10 frames per second every second image
    # is used: 0.0, 0.1, ..., 12.5 s.
    assert client.get(artifact["url"]).text == "FAKE-MP4 frames=126 size=160x90\n"
    assert not work_file(settings, body["jobId"], "frames").exists(), "frames must be deleted"

    argv = json.loads(work_file(settings, body["jobId"], "replayer.argv.json").read_text())
    assert argv.index("--headless") < argv.index("--window"), "off-screen capture needs --headless first (F6)"
    assert argv[argv.index("--window") + 1 : argv.index("--window") + 5] == ["0", "0", "160", "90"]
    assert "--fixed_timestep" not in argv, "no effect in the replayer's capture mode"
    assert argv[argv.index("--camera_mode") + 1] == "top"
    assert argv[argv.index("--gui") + 1] == "off"
    assert "--stop_time" not in argv
    assert argv[argv.index("--res_path") + 1] == str(settings.jobs_dir / body["jobId"] / "inputs")


def test_video_is_capped_at_max_video_seconds(tmp_path: Path, settings: Settings, scenario_bytes: bytes) -> None:
    capped = Settings(**{**settings.__dict__, "max_video_seconds": 5})
    with TestClient(create_app(capped)) as c:
        body = run(c, scenario_bytes, config=video_config())
        video_url = next(a["url"] for a in body["artifacts"] if a["name"] == "video.mp4")
        assert c.get(video_url).text.startswith("FAKE-MP4 frames=51 ")
    assert "covers only the first 5 s of 12.5 s" in body["warnings"][0]
    argv = json.loads(work_file(capped, body["jobId"], "replayer.argv.json").read_text())
    assert argv[argv.index("--stop_time") + 1] == "5000"


def test_failed_video_keeps_the_run_and_other_artifacts(client: TestClient, scenario_bytes: bytes) -> None:
    body = run(client, scenario_bytes, extra=[("fail-replayer.txt", b"x")], config=video_config())
    assert body["status"] == "succeeded"
    assert body["warnings"][0] == "no video: the replayer exited with code 3"
    assert [a["name"] for a in body["artifacts"]] == ["log.txt"]


def test_no_video_for_a_timed_out_run(client: TestClient) -> None:
    config = json.loads(video_config())
    config["limits"] = {"wallTimeoutSeconds": 1}
    body = run(client, scenario_with("fake:sleep=30"), name="slow.xosc", config=json.dumps(config))
    assert body["status"] == "timed_out"
    assert "no video: the run timed out, so its recording is incomplete" in body["warnings"]


def test_backlog_of_frames_on_disk_is_bounded(
    client: TestClient, scenario_bytes: bytes, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow encoder pauses the replayer, so frames do not pile up on disk."""
    monkeypatch.setattr(video, "MAX_BACKLOG", 5)
    monkeypatch.setattr(video, "RESUME_BACKLOG", 2)
    monkeypatch.setenv("FAKE_REPLAYER_DELAY", "0.005")  # writes 4x faster than the encoder reads
    monkeypatch.setenv("FAKE_FFMPEG_DELAY", "0.02")
    body = run(client, scenario_bytes, config=video_config())
    video_url = next(a["url"] for a in body["artifacts"] if a["name"] == "video.mp4")
    assert client.get(video_url).text.startswith("FAKE-MP4 frames=126 "), "no frame may be lost"
    replayer_log = work_file(settings, body["jobId"], "replayer_log.txt").read_text()
    peak = int(replayer_log.split("peak=")[1])
    # Without pausing about 95 of the 126 frames would pile up. The pump checks between frames, so the replayer
    # can write a few more (about the delay ratio) before it is stopped.
    assert peak <= 5 + 5, replayer_log


def test_video_options_are_validated(client: TestClient, scenario_bytes: bytes) -> None:
    for options, loc in [
        ({"width": 161}, ["config", "video", "width"]),  # H.264 needs even sizes
        ({"height": 2000}, ["config", "video", "height"]),
        ({"framesPerSecond": 0}, ["config", "video", "framesPerSecond"]),
        ({"cameraMode": "custom"}, ["config", "video", "cameraMode"]),
        ({"fps": 30}, ["config", "video", "fps"]),
    ]:
        response = submit(client, scenario_bytes, config=video_config(**options))
        assert response.status_code == 422, options
        assert response.json()["details"][0]["loc"] == loc, options


def test_video_request_without_tools_is_501(
    client: TestClient, scenario_bytes: bytes, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(simulation, "find_video_tools", lambda: None)
    response = submit(client, scenario_bytes, config=video_config())
    assert response.status_code == 501
    assert response.json()["code"] == "not_available"
    assert not any(settings.jobs_dir.iterdir()), "no job may be created for a rejected request"
    assert client.get("/v1/info").json()["capabilities"]["videos"] is False


def test_info_reports_media_capabilities_and_limits(client: TestClient) -> None:
    body = client.get("/v1/info").json()
    assert body["capabilities"]["videos"] is True
    assert body["capabilities"]["roadPreviews"] is True
    assert body["limits"]["maxVideoSeconds"] == 120
    assert body["limits"]["previewTimeoutSeconds"] == 60


@pytest.mark.parametrize(
    ("step", "fps", "expected"),
    [
        (0.05, 20, [1, 1, 1, 1, 1, 1]),  # one image per frame
        (0.05, 10, [1, 0, 1, 0, 1, 0]),  # every second image
        (0.05, 40, [1, 2, 2, 2, 2, 2]),  # repeated; frame j shows the image nearest to j / fps
        (0.1, 25, [2, 2, 3, 2, 3, 2]),  # 2.5 frames per image on average
    ],
)
def test_recorded_images_are_resampled_to_real_time(step: float, fps: int, expected: list[int]) -> None:
    written, counts = 0, []
    for recorded in range(len(expected)):
        counts.append(video.output_frames_for(recorded, written, step, fps))
        written += counts[-1]
    assert counts == expected


def test_video_frame_rate_differs_from_time_step(client: TestClient, scenario_bytes: bytes) -> None:
    config = json.loads(video_config(framesPerSecond=25))
    # 126 images (0.0 ... 12.5 s); at 25 fps frames 0 ... 313 (12.52 s) have one of them as nearest image.
    config["timestep"] = 0.1
    body = run(client, scenario_bytes, config=json.dumps(config))
    video_url = next(a["url"] for a in body["artifacts"] if a["name"] == "video.mp4")
    assert client.get(video_url).text.startswith("FAKE-MP4 frames=314 ")


def tga(width: int, height: int, *, image_type: int = 2, bits: int = 24, descriptor: int = 0, extra: int = 0) -> bytes:
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, image_type, 0, 0, 0, 0, 0, width, height, bits, descriptor)
    return header + bytes(width * height * 3 + extra)


def test_tga_frames_are_checked() -> None:
    options = VideoOptions.model_construct(width=4, height=2)  # below the API minimum, to keep frames tiny
    assert len(tga_pixels(tga(4, 2), options)) == 24
    for bad in [
        tga(4, 4),
        tga(4, 2, image_type=10),
        tga(4, 2, bits=32),
        tga(4, 2, descriptor=0x20),
        tga(4, 2, extra=1),
    ]:
        with pytest.raises(VideoError):
            tga_pixels(bad, options)


# --- road previews -----------------------------------------------------------------------------------------------


def preview(client: TestClient, content: bytes, name: str = "e6mini.xodr", config: str | None = None):
    data = {"config": config} if config is not None else None
    return client.post("/v1/road-previews", files=[("road", (name, content, "application/xml"))], data=data)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("renderer", ["odrviewer", "plot"])
def test_road_preview_returns_png(client: TestClient, road_bytes: bytes, settings: Settings, renderer: str) -> None:
    config = json.dumps({"renderer": renderer, "width": 640, "height": 480, "roadIds": False})
    response = preview(client, road_bytes, config=config)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert 'filename="e6mini.png"' in response.headers["content-disposition"]
    assert png_size(response.content) == (640, 480)
    assert not any((settings.data_dir / "previews").iterdir()), "the work directory must be deleted"


def test_road_preview_defaults_to_odrviewer(client: TestClient, road_bytes: bytes) -> None:
    from PIL import Image

    response = preview(client, road_bytes)
    assert response.status_code == 200, response.text
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (1200, 900)
    # The fake odrviewer's frame is blue with a red bottom row (TGA: BGR, bottom row first).
    assert image.getpixel((0, 899)) == (255, 0, 0)
    assert image.getpixel((0, 0)) == (0, 0, 255)


@pytest.mark.parametrize(
    ("name", "content", "status", "code"),
    [
        ("road.xosc", b"<OpenDRIVE/>", 415, "unsupported_file_type"),
        ("road.xodr", b'<?xml version="1.0"?><OpenSCENARIO/>', 422, "unexpected_root_element"),
        ("road.xodr", b"<OpenDRIVE><unclosed>", 422, "invalid_xml"),
        ("../road.xodr", b"<OpenDRIVE/>", 422, "invalid_file_name"),
        ("road.xodr", b"<OpenDRIVE><!-- fake:fail --></OpenDRIVE>", 422, "invalid_road"),
        ("road.xodr", b"<OpenDRIVE><!-- fake:empty --></OpenDRIVE>", 422, "invalid_road"),
    ],
)
def test_road_preview_rejects_bad_input(client: TestClient, name: str, content: bytes, status: int, code: str) -> None:
    response = preview(client, content, name=name)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_road_preview_config_is_validated(client: TestClient, road_bytes: bytes) -> None:
    response = preview(client, road_bytes, config='{"width": 50}')
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["config", "width"]
    response = preview(client, road_bytes, config='{"zoom": 2}')
    assert response.json()["details"][0]["loc"] == ["config", "zoom"]


@pytest.mark.parametrize("marker", ["fake:viewer-fail", "fake:viewer-blank"])
def test_road_preview_reports_odrviewer_failures(client: TestClient, settings: Settings, marker: str) -> None:
    response = preview(client, f"<OpenDRIVE><!-- {marker} --></OpenDRIVE>".encode(), name="bad.xodr")
    assert response.status_code == 500, response.text
    assert response.json()["code"] == "preview_failed"
    assert '"renderer": "plot"' in response.json()["message"]
    assert not any((settings.data_dir / "previews").iterdir())


def test_road_preview_odrviewer_timeout(tmp_path: Path, fake_esmini_home: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", esmini_home=fake_esmini_home, preview_timeout_s=1)
    with TestClient(create_app(settings)) as client:
        response = preview(client, b"<OpenDRIVE><!-- fake:viewer-sleep --></OpenDRIVE>", name="slow.xodr")
    assert response.status_code == 504, response.text
    assert response.json()["code"] == "timeout"


def test_road_preview_without_viewer_is_501(
    client: TestClient, road_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(road_preview, "viewer_available", lambda services: False)
    response = preview(client, road_bytes)
    assert response.status_code == 501
    assert response.json()["code"] == "not_available"
    assert preview(client, road_bytes, config='{"renderer": "plot"}').status_code == 200


def test_road_preview_rejects_unknown_renderer(client: TestClient, road_bytes: bytes) -> None:
    response = preview(client, road_bytes, config='{"renderer": "matplotlib"}')
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["config", "renderer"]


def border(xs: list[float], ys: list[float], z: float = 0.0) -> roadplot.Border:
    return roadplot.Border(road="1", section="0", lane=0, lane_type="none", x=xs, y=ys, z=[z] * len(xs))


def test_top_camera_frames_the_road_network() -> None:
    import math

    half_fov = math.tan(math.radians(0.5))
    # 400 m wide, 100 m high; at 4:3 the width decides: visible height 300 m (+5 %).
    x, y, z, rot = roadview.fit_top_camera([border([-100, 300], [0, 100], z=20)], 800, 600)
    assert (x, y, rot) == (100, 50, math.pi / 2)
    assert z == pytest.approx(20 + 300 * 1.05 / (2 * half_fov))
    # 100 m wide, 400 m high: the height decides.
    _, _, z, _ = roadview.fit_top_camera([border([0, 50], [0, 200]), border([100], [-200])], 800, 600)
    assert z == pytest.approx(400 * 1.05 / (2 * half_fov))
    # A single point still gives a sensible picture (at least 10 m).
    _, _, z, _ = roadview.fit_top_camera([border([5], [5])], 800, 600)
    assert z == pytest.approx(10 * 1.05 / (2 * half_fov))


def test_odrviewer_argv() -> None:
    argv = build_odrviewer_argv(Path("odrviewer"), Path("r.xodr"), 640, 480, (1.0, -2.5, 3000.0, 1.5))
    assert argv[argv.index("--custom_fixed_top_camera") + 1] == "1.0,-2.5,3000.0,1.5"
    assert argv.index("--headless") < argv.index("--window"), "PLAN.md F6: --headless first"
    assert argv[argv.index("--window") + 1 : argv.index("--window") + 5] == ["0", "0", "640", "480"]


def test_tga_frames_are_converted_and_checked() -> None:
    from PIL import Image

    data = tga(4, 2)
    image = Image.open(io.BytesIO(roadview.tga_to_png(data, 4, 2)))
    assert (image.size, image.mode) == ((4, 2), "RGB")
    for bad in [tga(4, 4), tga(4, 2, bits=32), tga(4, 2, descriptor=0x20), tga(4, 2, extra=1), data[:10]]:
        with pytest.raises(ValueError):
            roadview.tga_to_png(bad, 4, 2)


def test_road_preview_reports_odrplot_errors(client: TestClient) -> None:
    body = preview(client, b"<OpenDRIVE><!-- fake:fail --></OpenDRIVE>", name="bad.xodr").json()
    assert "[error] fake odrplot failure" in [d["message"] for d in body["details"]]


def test_odrplot_csv_is_parsed_into_borders(tmp_path: Path) -> None:
    source = tmp_path / "lanes.csv"
    source.write_text("lane, 3, 0, 0, none\n1.0, 2.0, 0.0, 0.0\n2.0, 3.0, 0.0, 0.0\nlane, 3, 0, -1, driving\n")
    borders = roadplot.read_borders(source)
    assert len(borders) == 1, "lanes without points are dropped"
    assert (borders[0].road, borders[0].lane, borders[0].x, borders[0].y) == ("3", 0, [1.0, 2.0], [2.0, 3.0])
    assert borders[0].z == [0.0, 0.0]
