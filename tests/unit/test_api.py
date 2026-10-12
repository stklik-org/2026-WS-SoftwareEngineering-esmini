"""API behaviour with a fake esmini (see conftest.py). The real esmini is exercised by tests/integration."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app
from app.services import simulation
from tests.unit.helpers import scenario_with


def submit(
    client: TestClient,
    scenario: bytes,
    name: str = "cut-in.xosc",
    config: str | None = None,
    extra: Sequence[tuple[str, bytes]] = (),
    scenario_field: str | None = None,
):
    files = [("files", (name, scenario, "application/xml"))]
    files += [("files", (n, content, "application/octet-stream")) for n, content in extra]
    data = {"scenario": name if scenario_field is None else scenario_field}
    if config is not None:
        data["config"] = config
    return client.post("/v1/simulations", files=files, data=data)


def wait_for(client: TestClient, job_id: str, timeout: float = 15) -> dict:
    """Poll the status until the job is finished, then return its result."""
    deadline = time.monotonic() + timeout
    while (state := client.get(f"/v1/jobs/{job_id}/status").json()["state"]) != "finished":
        assert state in ("queued", "running"), state
        assert time.monotonic() < deadline, f"job {job_id} did not finish within {timeout} s"
        time.sleep(0.02)
    response = client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def run(client: TestClient, scenario: bytes, **kwargs) -> dict:
    response = submit(client, scenario, **kwargs)
    assert response.status_code == 202, response.text
    return wait_for(client, response.json()["jobId"])


def wait_until(client: TestClient, job_id: str, state: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while client.get(f"/v1/jobs/{job_id}/status").json()["state"] != state:
        assert time.monotonic() < deadline, f"job {job_id} never became {state}"
        time.sleep(0.02)


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_info_reports_version_capabilities_and_limits(client: TestClient) -> None:
    body = client.get("/v1/info").json()
    assert body["esmini"] == {"version": "3.8.1", "gitRevision": "v3.8.1-0-19d26b68", "build": "6383"}
    assert body["imageVersion"] == "test"
    assert body["capabilities"]["simulations"] is True
    assert body["capabilities"]["rendering2d"] is False
    assert body["limits"]["maxConcurrentJobs"] == 2
    assert body["limits"]["maxTimeoutSeconds"] == 20


def test_root_redirects_to_swagger_ui(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


def test_submission_returns_202_and_job_urls(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["state"] in ("queued", "running", "finished")
    assert body["statusUrl"] == f"/v1/jobs/{body['jobId']}/status"
    assert body["resultUrl"] == f"/v1/jobs/{body['jobId']}"
    assert response.headers["location"] == body["statusUrl"]
    wait_for(client, body["jobId"])


def test_successful_run_returns_result_and_artifacts(
    client: TestClient, scenario_bytes: bytes, road_bytes: bytes
) -> None:
    body = run(client, scenario_bytes, extra=[("e6mini.xodr", road_bytes)], config='{"timestep": 0.1}')
    assert body["status"] == "succeeded"
    assert body["exitCode"] == 0
    assert body["scenario"] == "cut-in.xosc"
    assert body["esminiVersion"] == "3.8.1"
    assert body["simulatedSeconds"] == 12.5
    assert body["warnings"] == ["[warn] fake warning"]  # deduplicated
    assert body["config"]["timestep"] == 0.1
    assert [a["name"] for a in body["artifacts"]] == ["sim.dat", "sim.csv", "log.txt"]

    for artifact in body["artifacts"]:
        download = client.get(artifact["url"])
        assert download.status_code == 200
        assert len(download.content) == artifact["size"] > 0
        assert download.headers["content-type"].startswith(artifact["contentType"])

    status = client.get(f"/v1/jobs/{body['jobId']}/status").json()
    assert status["state"] == "finished"
    listing = client.get(f"/v1/jobs/{body['jobId']}/artifacts").json()
    assert {a["name"] for a in listing} == {"sim.dat", "sim.csv", "log.txt"}


def test_status_is_queued_then_running_then_finished(client: TestClient) -> None:
    """The test settings allow 2 concurrent jobs and 1 queued one."""
    slow = scenario_with("fake:sleep=1")
    first, second, third = (submit(client, slow, name=f"slow{i}.xosc").json()["jobId"] for i in range(3))
    wait_until(client, first, "running")
    wait_until(client, second, "running")
    assert client.get(f"/v1/jobs/{third}/status").json()["state"] == "queued"

    response = client.get(f"/v1/jobs/{third}")
    assert response.status_code == 409
    assert response.json()["code"] == "job_not_finished"

    busy = submit(client, slow, name="slow3.xosc")
    assert busy.status_code == 429
    assert busy.headers["retry-after"] == "5"

    for job_id in (first, second, third):
        assert wait_for(client, job_id)["status"] == "succeeded"
    assert submit(client, slow, name="slow4.xosc").status_code == 202, "capacity is available again"


def test_config_fields_reach_the_command_line(client: TestClient, scenario_bytes: bytes, settings: Settings) -> None:
    config = {"timestep": 0.02, "collisionDetection": True, "disableControllers": True, "seed": 7, "logLevel": "warn"}
    body = run(client, scenario_bytes, config=json.dumps(config))
    argv = json.loads((settings.jobs_dir / body["jobId"] / "work" / "sim.argv.json").read_text())
    assert argv[argv.index("--osc") + 1] == "cut-in.xosc"
    assert argv[argv.index("--fixed_timestep") + 1] == "0.02"
    assert argv[argv.index("--seed") + 1] == "7"
    assert argv[argv.index("--log_level") + 1] == "warn"
    assert "--collision" in argv and "--disable_controllers" in argv and "--headless" in argv


def test_named_scenario_is_run_among_several_xosc_files(
    client: TestClient, scenario_bytes: bytes, settings: Settings
) -> None:
    catalog = b'<?xml version="1.0"?><OpenSCENARIO><Catalog name="VehicleCatalog"/></OpenSCENARIO>'
    body = run(
        client,
        catalog,
        name="VehicleCatalog.xosc",
        extra=[("cut-in.xosc", scenario_bytes)],
        scenario_field="cut-in.xosc",
    )
    assert body["scenario"] == "cut-in.xosc"
    argv = json.loads((settings.jobs_dir / body["jobId"] / "work" / "sim.argv.json").read_text())
    assert argv[argv.index("--osc") + 1] == "cut-in.xosc"


def test_outputs_selects_artifacts(client: TestClient, scenario_bytes: bytes) -> None:
    body = run(client, scenario_bytes, config='{"outputs": ["csv"]}')
    assert [a["name"] for a in body["artifacts"]] == ["sim.csv"]
    assert body["simulatedSeconds"] == 12.5


def test_failed_run_is_reported_with_exit_code(client: TestClient) -> None:
    body = run(client, scenario_with("fake:exit=255"), name="bad.xosc")
    assert body["status"] == "failed"
    assert body["exitCode"] == 255
    assert body["warnings"][0] == "esmini exited with code 255"
    assert "[error] fake failure" in body["warnings"]
    assert "log.txt" in [a["name"] for a in body["artifacts"]]


def test_timeout_kills_the_run(client: TestClient) -> None:
    body = run(client, scenario_with("fake:sleep=30"), name="slow.xosc", config='{"limits": {"wallTimeoutSeconds": 1}}')
    assert body["status"] == "timed_out"
    assert body["exitCode"] is None
    assert 0.9 < body["wallSeconds"] < 5
    assert "was killed" in body["warnings"][0]


def test_internal_error_finishes_the_job_as_failed(
    client: TestClient, scenario_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(simulation, "_execute", broken)
    body = run(client, scenario_bytes)
    assert body["status"] == "failed"
    assert body["exitCode"] is None
    assert body["warnings"] == ["internal error of the service (RuntimeError); see the service log"]
    assert body["artifacts"] == []


def test_timeout_above_server_maximum_is_rejected(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, config='{"limits": {"wallTimeoutSeconds": 21}}')
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["config", "limits", "wallTimeoutSeconds"]


def test_unknown_config_field_is_rejected_and_named(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, config='{"timestep": 0.05, "duration": 10}')
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["config", "duration"]
    assert body["details"][0]["type"] == "extra_forbidden"


def test_snake_case_config_field_is_rejected_with_hint(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, config='{"collision_detection": true}')
    assert response.status_code == 422
    assert "collisionDetection" in response.json()["details"][0]["message"]


def test_invalid_json_config_is_rejected(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, config="{timestep: 0.05")
    assert response.status_code == 422
    assert response.json()["details"][0]["type"] == "json_invalid"


def test_missing_files_and_scenario_are_rejected(client: TestClient, scenario_bytes: bytes) -> None:
    response = client.post("/v1/simulations", data={"scenario": "cut-in.xosc"})
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["body", "files"]
    response = client.post("/v1/simulations", files=[("files", ("cut-in.xosc", scenario_bytes, "application/xml"))])
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["body", "scenario"]


def test_scenario_must_name_an_uploaded_file(client: TestClient, scenario_bytes: bytes, settings: Settings) -> None:
    response = submit(client, scenario_bytes, scenario_field="other.xosc")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "scenario_not_found"
    assert body["details"][0]["loc"] == ["scenario"]
    assert "'cut-in.xosc'" in body["message"]
    assert not any(settings.jobs_dir.iterdir()), "no job may be created for an invalid request"


def test_scenario_must_be_an_xosc_file(client: TestClient, scenario_bytes: bytes, road_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, extra=[("e6mini.xodr", road_bytes)], scenario_field="e6mini.xodr")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_file_type"


def test_models_and_other_files_are_stored(client: TestClient, scenario_bytes: bytes, settings: Settings) -> None:
    extra = [("car_white.osgb", b"\x00binary"), ("asphalt.jpg", b"\xff\xd8"), ("e6mini.sumocfg", b"<configuration/>")]
    body = run(client, scenario_bytes, extra=extra)
    inputs = settings.jobs_dir / body["jobId"] / "inputs"
    assert sorted(p.name for p in inputs.iterdir()) == [
        "asphalt.jpg",
        "car_white.osgb",
        "cut-in.xosc",
        "e6mini.sumocfg",
    ]
    assert (inputs / "car_white.osgb").read_bytes() == b"\x00binary"


def test_malformed_xml_is_rejected_before_esmini_runs(client: TestClient, settings: Settings) -> None:
    response = submit(client, b"<OpenSCENARIO><unclosed>", name="broken.xosc")
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_xml"
    assert not any(settings.jobs_dir.iterdir()), "no job may be created for an invalid request"


def test_unsafe_file_names_are_rejected(client: TestClient, scenario_bytes: bytes, settings: Settings) -> None:
    for name in ["../../etc/passwd.xosc", "x.xosc/../../y.xosc", "..\\evil.xosc", ".hidden.xosc", "a" * 200 + ".xosc"]:
        response = submit(client, scenario_bytes, name=name)
        assert response.status_code == 422, name
        assert response.json()["code"] == "invalid_file_name", name
    assert not any(settings.jobs_dir.iterdir())


def test_absurdly_long_file_name_is_rejected_by_the_multipart_parser(client: TestClient, scenario_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, name="a" * 10_000 + ".xosc")
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


def test_duplicate_file_names_are_rejected(client: TestClient, scenario_bytes: bytes, road_bytes: bytes) -> None:
    response = submit(client, scenario_bytes, extra=[("e6mini.xodr", road_bytes), ("e6mini.xodr", road_bytes)])
    assert response.status_code == 422
    assert response.json()["code"] == "duplicate_file_name"


def test_files_are_stored_flat_next_to_each_other(
    client: TestClient, scenario_bytes: bytes, road_bytes: bytes, settings: Settings
) -> None:
    catalog = b'<?xml version="1.0"?><OpenSCENARIO><Catalog name="VehicleCatalog"/></OpenSCENARIO>'
    extra = [("e6mini.xodr", road_bytes), ("VehicleCatalog.xosc", catalog), ("notes.txt", b"hello")]
    body = run(client, scenario_bytes, extra=extra)
    inputs = settings.jobs_dir / body["jobId"] / "inputs"
    assert sorted(p.name for p in inputs.iterdir()) == [
        "VehicleCatalog.xosc",
        "cut-in.xosc",
        "e6mini.xodr",
        "notes.txt",
    ]


def test_file_over_per_file_limit_is_413(client: TestClient) -> None:
    big = scenario_with("x" * 600_000)  # limit in the test settings: 0.5 MB per file, 1 MB in total
    response = submit(client, big, name="big.xosc")
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_request_over_total_limit_is_413_by_content_length(client: TestClient) -> None:
    response = submit(client, scenario_with("x" * 1_200_000), name="huge.xosc")
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_chunked_request_over_total_limit_is_413(client: TestClient) -> None:
    boundary = "testboundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="huge.xosc"\r\n'
        "Content-Type: application/xml\r\n\r\n"
    ).encode()

    def body():  # a generator makes httpx use chunked transfer encoding (no Content-Length)
        yield head
        for _ in range(30):
            yield b"x" * 50_000
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/v1/simulations", content=body(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_unknown_job_and_artifact_are_404(client: TestClient, scenario_bytes: bytes) -> None:
    assert client.get("/v1/jobs/doesnotexistdoesnotexi").json()["code"] == "job_not_found"
    assert client.get("/v1/jobs/doesnotexistdoesnotexi/status").json()["code"] == "job_not_found"
    assert client.get("/v1/jobs/..%2F..%2Fetc").status_code == 404
    job_id = run(client, scenario_bytes)["jobId"]
    for name in ["passwd", "..%2Fresult.json", "result.json", "..%2Finputs%2Fcut-in.xosc"]:
        response = client.get(f"/v1/jobs/{job_id}/artifacts/{name}")
        assert response.status_code == 404, name


def test_interrupted_job_has_a_state_but_no_result(client: TestClient) -> None:
    """A job directory without a result that no task works on, as left behind by a restart."""
    job = client.app.state.services.jobs.submit()
    client.app.state.services.jobs.finish(job)
    assert client.get(f"/v1/jobs/{job.id}/status").json()["state"] == "interrupted"
    response = client.get(f"/v1/jobs/{job.id}")
    assert response.status_code == 404
    assert "interrupted" in response.json()["message"]


def test_list_jobs_shows_every_state_newest_first(client: TestClient, scenario_bytes: bytes) -> None:
    assert client.get("/v1/jobs").json() == []
    finished = run(client, scenario_bytes)["jobId"]
    time.sleep(0.02)
    interrupted = client.app.state.services.jobs.submit()
    (interrupted.inputs / "x.xosc").write_bytes(b"x")  # like a submission
    client.app.state.services.jobs.finish(interrupted)
    time.sleep(0.02)
    slow = submit(client, scenario_with("fake:sleep=1"), name="slow.xosc").json()["jobId"]

    for path in ("/v1/jobs", "/v1/jobs/"):  # with and without trailing slash, no redirect
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, path
        listing = response.json()
        assert [j["jobId"] for j in listing] == [slow, interrupted.id, finished], path
    states = {j["jobId"]: j["state"] for j in listing}
    assert states[finished] == "finished" and states[interrupted.id] == "interrupted"
    assert states[slow] in ("queued", "running")
    assert listing[2]["resultUrl"] == f"/v1/jobs/{finished}" and listing[2]["statusUrl"].endswith("/status")
    assert listing[0]["submittedAt"] >= listing[1]["submittedAt"] >= listing[2]["submittedAt"]
    wait_for(client, slow)


def test_service_reports_missing_esmini(tmp_path: Path, scenario_bytes: bytes) -> None:
    settings = Settings(data_dir=tmp_path / "data", esmini_home=tmp_path / "missing")
    with TestClient(create_app(settings)) as c:
        response = submit(c, scenario_bytes)
    assert response.status_code == 503
    assert response.json()["code"] == "esmini_unavailable"


def test_config_may_be_sent_as_a_file_part(client: TestClient, scenario_bytes: bytes) -> None:
    files = [
        ("files", ("cut-in.xosc", scenario_bytes, "application/xml")),
        ("config", ("config.json", b'{"timestep": 0.2}', "application/json")),
    ]
    response = client.post("/v1/simulations", files=files, data={"scenario": "cut-in.xosc"})
    assert response.status_code == 202, response.text
    assert wait_for(client, response.json()["jobId"])["config"]["timestep"] == 0.2


def test_empty_optional_parts_as_sent_by_swagger_ui(client: TestClient, scenario_bytes: bytes) -> None:
    """Swagger UI's "Send empty value" sends empty strings for parts left blank."""
    files = [("files", ("cut-in.xosc", scenario_bytes, "application/xml"))]
    response = client.post("/v1/simulations", files=files, data={"scenario": "cut-in.xosc", "config": ""})
    assert response.status_code == 202, response.text
    assert wait_for(client, response.json()["jobId"])["status"] == "succeeded"


def test_swagger_ui_placeholder_in_files_is_ignored(client: TestClient, scenario_bytes: bytes) -> None:
    """Swagger UI sends `files=string` for a pre-filled list item that was left without a file."""
    files = [("files", ("cut-in.xosc", scenario_bytes, "application/xml"))]
    response = client.post("/v1/simulations", files=files, data={"scenario": "cut-in.xosc", "files": "string"})
    assert response.status_code == 202, response.text
    wait_for(client, response.json()["jobId"])


def test_only_placeholders_in_files_are_rejected(client: TestClient) -> None:
    response = client.post("/v1/simulations", data={"scenario": "cut-in.xosc", "files": "string"})
    assert response.status_code == 422
    assert response.json()["details"][0]["loc"] == ["files"]


def test_text_value_in_files_is_rejected(client: TestClient, scenario_bytes: bytes) -> None:
    files = [("files", ("cut-in.xosc", scenario_bytes, "application/xml"))]
    response = client.post("/v1/simulations", files=files, data={"scenario": "cut-in.xosc", "files": "<OpenSCENARIO/>"})
    assert response.status_code == 422
    assert "file upload" in response.json()["message"]
