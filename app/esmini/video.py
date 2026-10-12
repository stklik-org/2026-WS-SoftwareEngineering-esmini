"""Videos: replay a recording in esmini's 3D viewer off-screen and encode the frames as MP4 (FR-06).

Measured in docs/spikes/02-rendering.md:

- The viewer needs an X display; without one the replayer crashes. Each video gets its own `Xvfb`, started with
  `-displayfd`, so that Xvfb picks a free display number and reports when it is ready. Parallel jobs therefore
  never collide, and no `xvfb-run` or `xauth` is needed.
- `replayer --capture_screen` writes one uncompressed 24-bit TGA per frame (the format is fixed in esmini). At
  1280x720 that is 2.7 MB per frame, 1.2 GB for a 22 s scenario at 20 frames per second.
- The frames are therefore streamed: a frame is complete when the next one exists or the replayer has exited; it
  is then passed to `ffmpeg` as raw pixels and deleted. If ffmpeg falls behind, the replayer is paused (SIGSTOP)
  until the backlog has shrunk, so at most `MAX_BACKLOG` frames are on disk at any time.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import shutil
import signal
import struct
import subprocess
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.esmini.runner import wrap_command
from app.models.simulation import VideoOptions

TGA_HEADER_BYTES = 18
MAX_BACKLOG = 24  # frames on disk before the replayer is paused
RESUME_BACKLOG = 4  # frames on disk below which it continues
POLL_SECONDS = 0.01
XVFB_START_TIMEOUT_S = 10


class VideoError(Exception):
    """Rendering failed; the message is meant for the job's warnings."""


@dataclass(frozen=True)
class VideoTools:
    xvfb: str
    ffmpeg: str


def find_video_tools() -> VideoTools | None:
    """`None` if Xvfb or ffmpeg is missing (e.g. on a development machine without them)."""
    xvfb, ffmpeg = shutil.which("Xvfb"), shutil.which("ffmpeg")
    return VideoTools(xvfb=xvfb, ffmpeg=ffmpeg) if xvfb and ffmpeg else None


@dataclass(frozen=True)
class VideoResult:
    frames: int
    wall_seconds: float


def build_ffmpeg_argv(ffmpeg: str, video: VideoOptions, output: Path) -> list[str]:
    """Raw BGR frames on stdin (TGA pixel order, bottom row first, hence `vflip`) to H.264 MP4."""
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{video.width}x{video.height}",
        "-framerate",
        str(video.frames_per_second),
        "-i",
        "-",
        "-vf",
        "vflip",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]


def tga_pixels(data: bytes, video: VideoOptions) -> bytes:
    """The pixel bytes of an uncompressed 24-bit TGA of the expected size with bottom-left origin."""
    if len(data) < TGA_HEADER_BYTES:
        raise VideoError("the replayer wrote a truncated frame")
    id_length, _, image_type = data[0], data[1], data[2]
    width, height, bits, descriptor = struct.unpack_from("<HHBB", data, 12)
    expected = TGA_HEADER_BYTES + id_length + video.width * video.height * 3
    if (image_type, bits, width, height, descriptor & 0x20, len(data)) != (
        2,
        24,
        video.width,
        video.height,
        0,
        expected,
    ):
        raise VideoError(
            f"the replayer wrote an unexpected frame (type {image_type}, {bits} bit, {width}x{height}, "
            f"{len(data)} bytes)"
        )
    return data[TGA_HEADER_BYTES + id_length :]


async def render_video(
    replayer_argv: Sequence[str],
    *,
    tools: VideoTools,
    video: VideoOptions,
    frames_dir: Path,
    output: Path,
    log: Path,
    step_seconds: float,
    timeout: float,
    max_file_bytes: int,
) -> VideoResult:
    """Run the replayer under a private Xvfb and encode its frames into `output`. Raises `VideoError`.

    `step_seconds` is the time step of the recording: in capture mode the replayer renders one frame per recorded
    step (`GoToNextFrame` in replayer/main.cpp; its `--fixed_timestep` has no effect there).
    """
    start = time.monotonic()
    frames_dir.mkdir(parents=True, exist_ok=True)
    deadline = start + timeout
    async with xvfb_display(tools.xvfb, video.width, video.height, log, deadline) as display:
        env = {**os.environ, "DISPLAY": display, "LIBGL_ALWAYS_SOFTWARE": "1"}
        with log.open("ab") as out:
            replayer = await asyncio.create_subprocess_exec(
                *wrap_command(replayer_argv, max_file_bytes=max_file_bytes, line_buffered=False),
                cwd=frames_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            encoder = await asyncio.create_subprocess_exec(
                *wrap_command(
                    build_ffmpeg_argv(tools.ffmpeg, video, output), max_file_bytes=max_file_bytes, line_buffered=False
                ),
                cwd=frames_dir,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        try:
            remaining = max(0.1, deadline - time.monotonic())
            frames = await asyncio.wait_for(_pump(replayer, encoder, frames_dir, video, step_seconds), remaining)
        except asyncio.TimeoutError:
            raise VideoError(f"rendering the video did not finish within {timeout:g} s") from None
        finally:
            await _kill(replayer)
            await _kill(encoder)
    return VideoResult(frames=frames, wall_seconds=time.monotonic() - start)


def _frame(frames_dir: Path, index: int) -> Path:
    return frames_dir / f"screen_shot_{index:05d}.tga"


def output_frames_for(recorded: int, written: int, step_seconds: float, fps: int) -> int:
    """How often recorded frame `recorded` (at `recorded * step_seconds`) goes into the video, when `written`
    frames have been written so far: output frame j (at `j / fps`) shows the recorded frame nearest to its time.
    Frames are thereby dropped (fps below 1 / step) or repeated (above), so that the video plays in real time."""
    count = 0
    while math.floor((written + count) / (fps * step_seconds) + 0.5 + 1e-9) <= recorded:
        count += 1
    return count


@dataclass
class _Progress:
    next_frame: int = 0  # index of the next recorded frame to process


async def _pump(
    replayer: asyncio.subprocess.Process,
    encoder: asyncio.subprocess.Process,
    frames_dir: Path,
    video: VideoOptions,
    step_seconds: float,
) -> int:
    stdin = encoder.stdin
    if stdin is None:  # always set: the encoder is started with stdin=PIPE
        raise VideoError("ffmpeg has no input pipe")
    progress = _Progress()
    written = 0
    replayer_done = asyncio.ensure_future(replayer.wait())
    # A separate task, because the loop below may wait for ffmpeg (e.g. while it starts) for longer than the
    # replayer needs to write many frames.
    throttle = asyncio.ensure_future(_throttle(replayer, replayer_done, frames_dir, progress))
    try:
        while True:
            finished = replayer_done.done()
            current = _frame(frames_dir, progress.next_frame)
            if _frame(frames_dir, progress.next_frame + 1).exists() or (finished and current.exists()):
                repeat = output_frames_for(progress.next_frame, written, step_seconds, video.frames_per_second)
                if repeat:
                    pixels = tga_pixels(await asyncio.to_thread(current.read_bytes), video)
                    try:
                        for _ in range(repeat):
                            stdin.write(pixels)
                            await stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        raise VideoError("ffmpeg stopped while encoding; see the service log") from None
                    written += repeat
                current.unlink()
                progress.next_frame += 1
                continue
            if finished:
                break
            await asyncio.sleep(POLL_SECONDS)
    finally:
        throttle.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await throttle
        replayer_done.cancel()

    if replayer.returncode != 0:
        raise VideoError(f"the replayer exited with code {replayer.returncode}")
    if written == 0:
        raise VideoError("the replayer wrote no frames")
    stdin.close()
    if await encoder.wait() != 0:
        raise VideoError(f"ffmpeg exited with code {encoder.returncode}")
    return written


async def _throttle(
    replayer: asyncio.subprocess.Process, replayer_done: asyncio.Future, frames_dir: Path, progress: _Progress
) -> None:
    """Pause the replayer while `MAX_BACKLOG` frames wait on disk; continue below `RESUME_BACKLOG`."""
    paused = False
    try:
        while not replayer_done.done():
            if not paused and _frame(frames_dir, progress.next_frame + MAX_BACKLOG).exists():
                _signal(replayer, signal.SIGSTOP)
                paused = True
            elif paused and not _frame(frames_dir, progress.next_frame + RESUME_BACKLOG).exists():
                _signal(replayer, signal.SIGCONT)
                paused = False
            await asyncio.sleep(POLL_SECONDS)
    finally:
        if paused:
            _signal(replayer, signal.SIGCONT)


def _signal(proc: asyncio.subprocess.Process, sig: int) -> None:
    # PermissionError: macOS refuses signals to a process that has exited but is not yet reaped (Linux does not).
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, sig)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the process group unless the process has already been reaped (its PID may have been reused then)."""
    if proc.returncode is None:
        _signal(proc, signal.SIGKILL)
        await proc.wait()


@contextlib.asynccontextmanager
async def xvfb_display(xvfb: str, width: int, height: int, log: Path, deadline: float) -> AsyncIterator[str]:
    """A private X server with a screen of at least `width` x `height`; yields the value for `DISPLAY`. Raises
    `VideoError` if it does not start before `deadline` (`time.monotonic()`)."""
    read_fd, write_fd = os.pipe()
    try:
        with log.open("ab") as out:
            proc = await asyncio.create_subprocess_exec(
                xvfb,
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                f"{width}x{height}x24",
                "-nolisten",
                "tcp",
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(write_fd,),
            )
    finally:
        os.close(write_fd)
    try:
        wait = max(0.1, min(XVFB_START_TIMEOUT_S, deadline - time.monotonic()))
        try:
            number = await asyncio.wait_for(asyncio.to_thread(_read_line, read_fd), wait)
        except asyncio.TimeoutError:
            raise VideoError(f"the virtual display (Xvfb) did not start within {wait:g} s") from None
        if not number.isdigit():
            raise VideoError("the virtual display (Xvfb) did not start; see the service log")
        yield f":{number}"
    finally:
        await _kill(proc)  # also ends a blocked _read_line: the pipe's write end closes
        os.close(read_fd)


def _read_line(fd: int) -> str:
    data = b""
    while not data.endswith(b"\n"):
        chunk = os.read(fd, 64)
        if not chunk:
            break
        data += chunk
    return data.decode(errors="replace").strip()
