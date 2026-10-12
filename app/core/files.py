"""Validation of uploaded files before esmini sees them (PLAN.md §5.3, §6.7).

File names are *rejected*, not rewritten, when they are unsafe: a scenario refers to its road and catalogs by
file name, so silently renaming an upload would break those references.

All files arrive in one list (`files`); the request names the scenario among them (`scenario`). The image contains
no esmini sample content, so everything a scenario refers to must be in the list: roads, catalogs, SUMO files,
3D models (`.osgb`), textures, ... Any file type is accepted. esmini only *reads* such files: it loads no code from
a scenario (no `dlopen`/`LoadLibrary` anywhere in its source; all controllers are built in). XML files are parsed
defensively before esmini sees them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree.ElementTree import ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.core.errors import ApiError

SCENARIO_EXTENSION = ".xosc"
XML_EXTENSIONS = frozenset({".xosc", ".xodr", ".xml", ".sumocfg"})
EXPECTED_ROOT = {".xosc": "OpenSCENARIO", ".xodr": "OpenDRIVE"}
MAX_NAME_LENGTH = 128
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. +()-]*$")


@dataclass(frozen=True)
class InputFile:
    name: str
    content: bytes

    @property
    def extension(self) -> str:
        return PurePosixPath(self.name).suffix.lower()


LOC = ["files"]


def check_name(name: str | None, loc: list[str] = LOC) -> None:
    if not name:
        raise ApiError(
            422,
            "invalid_file_name",
            f"A part of `{loc[0]}` has no file name.",
            [{"loc": loc, "message": "a file name is required", "type": "missing"}],
        )
    problem = None
    if len(name) > MAX_NAME_LENGTH:
        problem = f"longer than {MAX_NAME_LENGTH} characters"
    elif "/" in name or "\\" in name:
        problem = "contains a path separator; send the bare file name"
    elif not _SAFE_NAME.match(name) or name.endswith((" ", ".")):
        problem = "may only contain letters, digits, space and . _ + - ( ), and must start with a letter or digit"
    if problem:
        shown = name if len(name) <= 60 else name[:57] + "..."
        raise ApiError(
            422,
            "invalid_file_name",
            f"File name {shown!r} is not accepted: {problem}.",
            [{"loc": loc, "message": problem, "type": "invalid_file_name"}],
        )


def check_scenario(files: Sequence[InputFile], scenario: str) -> None:
    """The scenario must be one of the uploaded files and an OpenSCENARIO file."""
    loc = ["scenario"]
    if not scenario.strip():
        raise ApiError(
            422,
            "validation_error",
            "The scenario field is empty; give the file name of the scenario among the uploaded files.",
            [{"loc": loc, "message": "a file name is required", "type": "missing"}],
        )
    if scenario not in {f.name for f in files}:
        uploaded = ", ".join(repr(f.name) for f in files)
        raise ApiError(
            422,
            "scenario_not_found",
            f"The scenario {scenario!r} is not among the uploaded files ({uploaded}).",
            [{"loc": loc, "message": "no uploaded file has this name", "type": "scenario_not_found"}],
        )
    if PurePosixPath(scenario).suffix.lower() != SCENARIO_EXTENSION:
        raise ApiError(
            415,
            "unsupported_file_type",
            f"The scenario {scenario!r} must be an OpenSCENARIO file ({SCENARIO_EXTENSION}).",
            [{"loc": loc, "message": "extension not allowed", "type": "unsupported_file_type"}],
        )


def check_xml(file: InputFile, loc: list[str] = LOC) -> None:
    """Parse with defusedxml (no entity expansion, no external references) and check the root element."""
    try:
        root = fromstring(file.content)
    except DefusedXmlException as exc:
        raise ApiError(
            422,
            "forbidden_xml",
            f"{file.name!r} uses a forbidden XML construct ({exc}).",
            [{"loc": loc, "message": str(exc), "type": "forbidden_xml"}],
        ) from None
    except ParseError as exc:
        raise ApiError(
            422,
            "invalid_xml",
            f"{file.name!r} is not well-formed XML: {exc}.",
            [{"loc": loc, "message": str(exc), "type": "invalid_xml"}],
        ) from None
    expected = EXPECTED_ROOT.get(file.extension)
    tag = root.tag.rsplit("}", 1)[-1]
    if expected and tag != expected:
        raise ApiError(
            422,
            "unexpected_root_element",
            f"{file.name!r} has root element <{tag}>, expected <{expected}>.",
            [{"loc": loc, "message": f"expected <{expected}>", "type": "unexpected_root_element"}],
        )


def validate_input_files(files: Sequence[InputFile], scenario: str, max_file_bytes: int) -> None:
    """Raise `ApiError` for the first problem found; order: names, duplicates, scenario, sizes, content."""
    if not files:
        raise ApiError(
            422,
            "validation_error",
            "No files were uploaded; send the scenario and every file it refers to as `files`.",
            [{"loc": LOC, "message": "at least one file is required", "type": "missing"}],
        )
    for file in files:
        check_name(file.name)
    seen: set[str] = set()
    for file in files:
        if file.name in seen:
            raise ApiError(
                422,
                "duplicate_file_name",
                f"More than one uploaded file is named {file.name!r}.",
                [{"loc": LOC, "message": "duplicate file name", "type": "duplicate_file_name"}],
            )
        seen.add(file.name)
    check_scenario(files, scenario)
    for file in files:
        if len(file.content) > max_file_bytes:
            raise ApiError(
                413,
                "payload_too_large",
                f"{file.name!r} exceeds the per-file limit of {max_file_bytes / (1024 * 1024):g} MB.",
            )
        if not file.content.strip():
            raise ApiError(
                422,
                "empty_file",
                f"{file.name!r} is empty.",
                [{"loc": LOC, "message": "file is empty", "type": "empty_file"}],
            )
    for file in files:
        if file.extension in XML_EXTENSIONS:
            check_xml(file)


def validate_road_file(file: InputFile, max_file_bytes: int) -> None:
    """A single OpenDRIVE file, as for road previews; errors point at the `road` part."""
    loc = ["road"]
    check_name(file.name, loc)
    if file.extension != ".xodr":
        raise ApiError(
            415,
            "unsupported_file_type",
            f"{file.name!r} is not accepted; the road must be an OpenDRIVE file (.xodr).",
            [{"loc": loc, "message": "extension not allowed", "type": "unsupported_file_type"}],
        )
    if len(file.content) > max_file_bytes:
        raise ApiError(
            413,
            "payload_too_large",
            f"{file.name!r} exceeds the per-file limit of {max_file_bytes / (1024 * 1024):g} MB.",
        )
    if not file.content.strip():
        raise ApiError(
            422,
            "empty_file",
            f"{file.name!r} is empty.",
            [{"loc": loc, "message": "file is empty", "type": "empty_file"}],
        )
    check_xml(file, loc)
