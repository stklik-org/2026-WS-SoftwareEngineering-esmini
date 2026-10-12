"""Runs one external process per job with a wall-clock timeout (PLAN.md D2, §6.7).

Each process starts in a new session (and therefore a new process group), so that a timeout or a cancelled
request kills the process together with anything it may have spawned.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

KILL_GRACE_SECONDS = 2.0


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None  # None if the process was killed because of the timeout
    timed_out: bool
    wall_seconds: float


def wrap_command(argv: Sequence[str], *, max_file_bytes: int | None, line_buffered: bool) -> list[str]:
    """Prefix helpers that each set up the process and then exec the next command in the same process, so that
    exit codes and signals are those of the command itself:

    - `prlimit --fsize` (util-linux) caps the size of every file the command writes, so that a runaway recording
      cannot fill the disk; exceeding it terminates the command with SIGXFSZ.
    - `stdbuf -oL -eL` (coreutils) makes stdout line-buffered. Without it esmini buffers its console output in
      4 KiB blocks when writing to a file, and the log of a run killed on timeout loses its last lines, which are
      the ones that explain the timeout (docs/spikes/01-release-archive.md).

    Both tools are present in the container; on development hosts without them (macOS) they are skipped.
    """
    command = list(argv)
    stdbuf = shutil.which("stdbuf") if line_buffered else None
    if stdbuf:
        command = [stdbuf, "-oL", "-eL", *command]
    prlimit = shutil.which("prlimit") if max_file_bytes is not None else None
    if prlimit:
        command = [prlimit, f"--fsize={max_file_bytes}", "--", *command]
    return command


def _kill_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, sig)


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    _kill_group(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        _kill_group(proc.pid, signal.SIGKILL)
        await proc.wait()


async def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    output: Path,
    max_file_bytes: int | None = None,
    line_buffered: bool = False,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run `argv` without a shell, appending stdout and stderr to `output`. `env` replaces the environment."""
    command = wrap_command(argv, max_file_bytes=max_file_bytes, line_buffered=line_buffered)
    start = time.monotonic()
    with output.open("ab") as out:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        await _terminate(proc)
        return ProcessResult(returncode=None, timed_out=True, wall_seconds=time.monotonic() - start)
    except asyncio.CancelledError:
        # The server shuts down or the request task is cancelled: do not leave orphans behind.
        await _terminate(proc)
        raise
    # No group kill after a normal exit: the leader has been reaped, so its PID (= group id) may already have
    # been reused by the next job's process.
    return ProcessResult(returncode=returncode, timed_out=False, wall_seconds=time.monotonic() - start)
