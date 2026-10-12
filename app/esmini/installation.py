"""Location and version of the esmini installation inside the image (`ESMINI_HOME`, default /opt/esmini)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

_VERSION_LINE = re.compile(r'^\s*(\w+)\s*=\s*"?([^"]*)"?\s*$')


@dataclass(frozen=True)
class EsminiVersion:
    version: str
    git_revision: str | None = None
    build: str | None = None


class EsminiInstallation:
    def __init__(self, home: Path) -> None:
        self.home = home

    @property
    def bin_dir(self) -> Path:
        return self.home / "bin"

    @property
    def esmini(self) -> Path:
        return self.bin_dir / "esmini"

    @property
    def dat2csv(self) -> Path:
        return self.bin_dir / "dat2csv"

    @property
    def replayer(self) -> Path:
        return self.bin_dir / "replayer"

    @property
    def odrplot(self) -> Path:
        return self.bin_dir / "odrplot"

    @property
    def odrviewer(self) -> Path:
        return self.bin_dir / "odrviewer"

    def is_available(self) -> bool:
        return self.esmini.is_file() and self.dat2csv.is_file()

    @cached_property
    def version(self) -> EsminiVersion:
        """Parsed from `version.txt` of the release archive, e.g. `ESMINI_GIT_TAG="v3.8.1"`."""
        path = self.home / "version.txt"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return EsminiVersion(version="unknown")
        values = {m.group(1): m.group(2) for line in text.splitlines() if (m := _VERSION_LINE.match(line))}
        tag = values.get("ESMINI_GIT_TAG", "")
        return EsminiVersion(
            version=tag.removeprefix("v") or "unknown",
            git_revision=values.get("ESMINI_GIT_REV"),
            build=values.get("ESMINI_BUILD_VERSION"),
        )
