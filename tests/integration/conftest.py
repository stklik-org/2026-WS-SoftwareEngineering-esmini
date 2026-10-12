"""Integration tests run against a real container (PLAN.md §12, level L2).

`make test-integration` builds the image, starts a dedicated container on port 18080 and sets
`ESMINI_URL` and `ESMINI_CONTAINER`. To run against a container you started yourself:

    ESMINI_URL=http://127.0.0.1:8080 ESMINI_CONTAINER=<name> uv run pytest -m integration tests/integration

Without `ESMINI_CONTAINER` the tests that inspect the container from the inside are skipped.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import DATA

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("ESMINI_URL", "http://127.0.0.1:8080").rstrip("/")


@pytest.fixture(scope="session")
def client(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=120) as c:
        deadline = time.monotonic() + 30
        while True:
            try:
                if c.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"No esmini service at {base_url}; start one with `make up` or use `make test-integration`")
            time.sleep(0.5)
        yield c


class Container:
    def __init__(self, name: str) -> None:
        self.name = name

    def exec(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["docker", "exec", self.name, *argv], capture_output=True, text=True, timeout=60)

    def python(self, code: str) -> str:
        result = self.exec("python3", "-c", code)
        assert result.returncode == 0, result.stderr
        return result.stdout


@pytest.fixture(scope="session")
def container() -> Container:
    name = os.environ.get("ESMINI_CONTAINER")
    if not name or shutil.which("docker") is None:
        pytest.skip("set ESMINI_CONTAINER to the container name to run in-container checks")
    return Container(name)


# cut-in.xosc and the scenarios derived from it need their road and vehicle catalog; the image bundles neither.
CUT_IN_FILES = ("e6mini.xodr", "VehicleCatalog.xosc")


def submit(client: httpx.Client, scenario: str, *others: str, config: str | None = None) -> httpx.Response:
    """POST the scenario and the named fixture files; answers 202 with the job's URLs if accepted."""
    files = [("files", (name, (DATA / name).read_bytes(), "application/xml")) for name in (scenario, *others)]
    data = {"scenario": scenario}
    if config:
        data["config"] = config
    return client.post("/v1/simulations", files=files, data=data)


def wait_for(client: httpx.Client, job_id: str, timeout: float = 120) -> dict:
    """Poll the status until the job is finished, then return its result."""
    deadline = time.monotonic() + timeout
    while (state := client.get(f"/v1/jobs/{job_id}/status").json()["state"]) != "finished":
        assert state in ("queued", "running"), state
        assert time.monotonic() < deadline, f"job {job_id} did not finish within {timeout} s"
        time.sleep(0.2)
    response = client.get(f"/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def simulate(client: httpx.Client, scenario: str, *others: str, config: str | None = None) -> dict:
    """Submit and wait: the result of a finished job."""
    response = submit(client, scenario, *others, config=config)
    assert response.status_code == 202, response.text
    return wait_for(client, response.json()["jobId"])


def data_file(name: str) -> Path:
    return DATA / name
