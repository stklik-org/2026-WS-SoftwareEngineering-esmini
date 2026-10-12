"""Reading esmini outputs: warnings from the console log, simulated time from the dat2csv CSV."""

from __future__ import annotations

import re
from pathlib import Path

# esmini log lines look like `[22.000] [info] Closing` or `[] [error] Couldn't locate OpenSCENARIO file x.xosc`.
_LOG_LINE = re.compile(r"^\[[^\]]*\]\s*\[(warn|warning|error)\]\s*(.+?)\s*$", re.IGNORECASE)
MAX_WARNINGS = 20


def log_warnings(log: Path, limit: int = MAX_WARNINGS) -> list[str]:
    """Distinct warn/error messages in order of first occurrence, truncated to `limit` entries."""
    if not log.is_file():
        return []
    seen: dict[str, None] = {}
    with log.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            match = _LOG_LINE.match(line)
            if match:
                seen.setdefault(f"[{match.group(1).lower()}] {match.group(2)}", None)
    messages = list(seen)
    if len(messages) > limit:
        return [*messages[:limit], f"... {len(messages) - limit} further distinct messages, see log.txt"]
    return messages


def last_simulation_time(csv: Path) -> float | None:
    """Value of the `time` column in the last row of a `dat2csv` file; the column is located by name."""
    if not csv.is_file():
        return None
    with csv.open("rb") as f:
        header = f.readline().decode("utf-8", errors="replace")
        columns = [c.strip().lower() for c in header.split(",")]
        if "time" not in columns:
            return None
        index = columns.index("time")
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 8192))
        tail = f.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(tail):
        fields = [x.strip() for x in line.split(",")]
        if len(fields) > index and fields[index] and line.strip() != header.strip():
            try:
                return float(fields[index])
            except ValueError:
                return None
    return None
