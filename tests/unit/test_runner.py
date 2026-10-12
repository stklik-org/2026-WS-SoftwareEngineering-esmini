"""Process runner: exit codes, output capture, timeout and process-group kill (NFR-04, AT-10)."""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from app.esmini import runner
from app.esmini.runner import run_process, wrap_command


def run(argv: list[str], tmp_path: Path, timeout: float = 10, **kwargs) -> runner.ProcessResult:
    return asyncio.run(run_process(argv, cwd=tmp_path, timeout=timeout, output=tmp_path / "out.txt", **kwargs))


def test_exit_code_and_output_are_captured(tmp_path: Path) -> None:
    result = run(
        [sys.executable, "-c", "import sys; print('hello', flush=True); print('oops', file=sys.stderr); sys.exit(3)"],
        tmp_path,
    )
    assert result.returncode == 3
    assert not result.timed_out
    assert (tmp_path / "out.txt").read_text().split() == ["hello", "oops"]


def test_runs_in_the_given_directory(tmp_path: Path) -> None:
    run([sys.executable, "-c", "import os; print(os.getcwd())"], tmp_path)
    assert Path((tmp_path / "out.txt").read_text().strip()).resolve() == tmp_path.resolve()


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    result = run([sys.executable, "-c", script], tmp_path, timeout=1.5)
    assert result.timed_out
    assert result.returncode is None
    assert time.monotonic() - start < 1.5 + runner.KILL_GRACE_SECONDS + 2
    child = int(child_pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"child process {child} survived the timeout")


def test_process_ignoring_sigterm_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "KILL_GRACE_SECONDS", 0.5)
    script = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    )
    start = time.monotonic()
    result = run([sys.executable, "-c", script], tmp_path, timeout=1)
    assert result.timed_out
    assert time.monotonic() - start < 4


def test_wrap_command_adds_available_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert wrap_command(["esmini", "--headless"], max_file_bytes=1024, line_buffered=True) == [
        "/usr/bin/prlimit",
        "--fsize=1024",
        "--",
        "/usr/bin/stdbuf",
        "-oL",
        "-eL",
        "esmini",
        "--headless",
    ]
    assert wrap_command(["esmini"], max_file_bytes=None, line_buffered=False) == ["esmini"]


def test_wrap_command_skips_missing_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    assert wrap_command(["esmini"], max_file_bytes=1024, line_buffered=True) == ["esmini"]
