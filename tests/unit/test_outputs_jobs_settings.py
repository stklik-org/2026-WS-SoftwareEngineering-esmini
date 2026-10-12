import asyncio
import os
import time
from pathlib import Path

import pytest

from app.core.errors import ApiError
from app.core.jobs import RESULT_FILE, Job, JobStore
from app.core.settings import Settings
from app.esmini.installation import EsminiInstallation
from app.esmini.outputs import last_simulation_time, log_warnings
from app.models.simulation import JobState

# --- esmini outputs ----------------------------------------------------------------------------------------------


def test_log_warnings_are_distinct_ordered_and_truncated(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    lines = ["[] [info] Loaded x", "[0.000] [warn] first", "[] [error] second", "[1.000] [warn] first"]
    lines += [f"[] [warn] w{i}" for i in range(30)]
    log.write_text("\n".join(lines) + "\n")
    warnings = log_warnings(log, limit=5)
    assert warnings[:2] == ["[warn] first", "[error] second"]
    assert len(warnings) == 6
    assert warnings[-1] == "... 27 further distinct messages, see log.txt"


def test_log_warnings_of_missing_log(tmp_path: Path) -> None:
    assert log_warnings(tmp_path / "none.txt") == []


def test_last_simulation_time_uses_the_time_column_by_name(tmp_path: Path) -> None:
    csv = tmp_path / "sim.csv"
    csv.write_text("id, name, time, x\n0, Ego, 0.000, 1\n1, T, 0.000, 2\n0, Ego, 21.950, 3\n1, T, 22.000, 4\n\n")
    assert last_simulation_time(csv) == 22.0


def test_last_simulation_time_without_rows_or_column(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("time, id\n")
    (tmp_path / "b.csv").write_text("t, id\n1.0, 0\n")
    assert last_simulation_time(tmp_path / "a.csv") is None
    assert last_simulation_time(tmp_path / "b.csv") is None
    assert last_simulation_time(tmp_path / "missing.csv") is None


def test_esmini_version_from_version_txt(tmp_path: Path) -> None:
    (tmp_path / "version.txt").write_text('ESMINI_GIT_REV="v3.8.1-0-19d26b68"\nESMINI_GIT_TAG="v3.8.1"\n')
    version = EsminiInstallation(tmp_path).version
    assert (version.version, version.git_revision, version.build) == ("3.8.1", "v3.8.1-0-19d26b68", None)
    assert EsminiInstallation(tmp_path / "missing").version.version == "unknown"


# --- jobs --------------------------------------------------------------------------------------------------------


def test_admission_limit_returns_429_with_retry_after(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=1, max_queued=1, ttl_seconds=60)
    first, second = store.submit(), store.submit()  # capacity 1 + 1: both are admitted at submission
    with pytest.raises(ApiError) as info:
        store.submit()
    assert info.value.status_code == 429
    assert info.value.headers == {"Retry-After": "5"}
    store.finish(first)
    store.submit()  # capacity is available again
    assert second.dir.is_dir()


def test_states_follow_the_life_cycle(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=1, max_queued=1, ttl_seconds=60)

    async def scenario() -> None:
        first, second = store.submit(), store.submit()
        assert store.state(first) == store.state(second) == JobState.QUEUED
        release = asyncio.Event()

        async def hold(job: Job) -> None:
            async with store.slot(job):
                await release.wait()
            (job.dir / RESULT_FILE).write_text("{}")
            store.finish(job)

        tasks = [asyncio.create_task(hold(first)), asyncio.create_task(hold(second))]
        await asyncio.sleep(0.05)
        assert store.state(first) == JobState.RUNNING
        assert store.state(second) == JobState.QUEUED  # waits for the only slot
        release.set()
        await asyncio.gather(*tasks)
        assert store.state(first) == store.state(second) == JobState.FINISHED

    asyncio.run(scenario())


def test_job_without_result_or_task_is_interrupted(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=1, max_queued=1, ttl_seconds=60)
    job = store.submit()
    store.finish(job)  # abandoned, e.g. cancelled on shutdown
    assert store.state(job) == JobState.INTERRUPTED


def test_list_jobs_ignores_other_directories(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=3, max_queued=0, ttl_seconds=60)
    first = store.submit()
    past = time.time() - 100
    os.utime(first.inputs, (past, past))
    second = store.submit()
    (tmp_path / "not-a-job").mkdir()
    (tmp_path / ("x" * 22)).mkdir()  # well-formed id, but no inputs: not a job
    assert store.list_jobs() == [second, first]


def test_concurrency_is_bounded_by_the_semaphore(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=2, max_queued=10, ttl_seconds=60)
    peak = current = 0

    async def job() -> None:
        nonlocal peak, current
        async with store.slot(store.submit()):
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.02)
            current -= 1

    async def scenario() -> None:
        await asyncio.gather(*(job() for _ in range(10)))

    asyncio.run(scenario())
    assert peak == 2


def test_cancel_all_cancels_background_work(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=1, max_queued=1, ttl_seconds=60)
    cancelled = []

    async def work() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def scenario() -> None:
        store.start(work())
        await asyncio.sleep(0.01)
        await store.cancel_all()

    asyncio.run(scenario())
    assert cancelled == [True]


def test_cleanup_removes_only_expired_finished_jobs(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=3, max_queued=0, ttl_seconds=100)
    old, fresh, running = store.submit(), store.submit(), store.submit()
    store.finish(old)
    store.finish(fresh)
    past = time.time() - 1000
    for job in (old, running):
        os.utime(job.dir, (past, past))
    (tmp_path / "not-a-job").mkdir()
    os.utime(tmp_path / "not-a-job", (past, past))

    assert store.cleanup_expired() == 1
    assert not old.dir.exists()
    assert fresh.dir.exists() and running.dir.exists() and (tmp_path / "not-a-job").exists()


def test_job_lookup_rejects_malformed_ids(tmp_path: Path) -> None:
    store = JobStore(tmp_path, max_concurrent=1, max_queued=0, ttl_seconds=100)
    job = store.submit()
    assert store.get(job.id) == job
    assert store.get("../" + job.id) is None
    assert store.get("x" * 22) is None  # well-formed but unknown


# --- settings ----------------------------------------------------------------------------------------------------


def test_settings_from_environment() -> None:
    s = Settings.from_env(
        {"PORT": "9000", "MAX_UPLOAD_MB": "5", "CORS_ORIGINS": "http://a:1, http://b:2", "LOG_LEVEL": "DEBUG"}
    )
    assert s.port == 9000
    assert s.max_upload_bytes == 5 * 1024 * 1024
    assert s.cors_origins == ("http://a:1", "http://b:2")
    assert s.log_level == "debug"
    assert Settings.from_env({}).port == 8080


@pytest.mark.parametrize(
    "env",
    [{"PORT": "abc"}, {"MAX_CONCURRENT_JOBS": "0"}, {"DEFAULT_TIMEOUT_S": "400"}, {"LOG_LEVEL": "loud"}],
)
def test_invalid_settings_fail_early(env: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        Settings.from_env(env)
