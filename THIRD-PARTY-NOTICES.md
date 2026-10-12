# Third-party notices

The code in this repository is licensed under the MIT Licence (see `LICENSE`). The container image built from it also
contains third-party software under its own licences, listed here. This file is copied into the image as
`/usr/share/doc/esmini-rest/THIRD-PARTY-NOTICES.md`.

## esmini (MPL-2.0)

- Project: <https://github.com/esmini/esmini>, copyright the esmini contributors.
- Version: tag `v3.8.1`, commit `19d26b6` (source: <https://github.com/esmini/esmini/tree/v3.8.1>).
- Licence: Mozilla Public Licence 2.0, <https://www.mozilla.org/MPL/2.0/>.
- Use here: the official `esmini-demo_Linux.zip` release archive is downloaded at build time, its SHA-256 is verified
  (`docker/Dockerfile`), and its contents are installed unmodified in `/opt/esmini`, except that `config.yml`,
  `resources`, `run`, `Hello-World_coding-example` and `EnvironmentSimulator` are deleted. The source of the binaries is available at the
  link above.
- Derived files: the test fixtures in `tests/data/` are derived from esmini scenarios and remain under MPL-2.0 (see
  `tests/data/NOTICE.md`). Any code adapted from esmini's own files (for example `xodr.py`) must keep its MPL-2.0
  header and attribution.

## Libraries bundled in the esmini binaries

The esmini release archive links the libraries below. The archive ships their licence texts in
`/opt/esmini/3rd_party_terms_and_licenses/` inside the image; they are kept there unchanged. Upstream's index of
the libraries is `esmini-src/3rd_party_terms_and_licenses/links.txt`.

| Component | Project |
|---|---|
| OpenSceneGraph | <https://github.com/openscenegraph/OpenSceneGraph> |
| OpenSimulationInterface (OSI) | <https://github.com/OpenSimulationInterface> |
| Protobuf | <https://github.com/protocolbuffers/protobuf> |
| pugixml | <https://pugixml.org/> |
| SUMO | <https://github.com/eclipse/sumo> |
| PROJ | <https://proj.org/> |
| fmt, spdlog | <https://github.com/fmtlib/fmt>, <https://github.com/gabime/spdlog> |
| GLFW, Dear ImGui, ImPlot | <https://www.glfw.org/>, <https://github.com/ocornut/imgui>, <https://github.com/epezent/implot> |
| expr, Clothoids, odrSpiral, JRC-FSM, Tiny YAML, FMI headers, dirent | see the licence files in `3rd_party_terms_and_licenses/` |

Which of these are linked into a given binary depends on how upstream built the release; check the licence directory
of the pinned archive rather than this table when you need an exact list.

## 3D models and example data in the esmini archive

The demo archive also contains a 3D model pack and example scenarios, roads and catalogues
(`resources/`). The build deletes this directory, so the image contains none of them; a simulation uses only the
files the client uploads. (The licence of the model pack is not stated upstream, which is a further reason not to
ship it.)

## Python dependencies

Installed from `uv.lock` into `/opt/venv`. The direct dependencies are permissively licensed: FastAPI, Uvicorn,
python-multipart (MIT or BSD-style), defusedxml (Python Software Foundation licence) and matplotlib (the
PSF-based Matplotlib licence; it brings NumPy, BSD-3-Clause, and Pillow, MIT-CMU). Their
transitive dependencies (for example Starlette, Pydantic and AnyIO) are listed in `uv.lock`; each package carries
its licence in its `*.dist-info` directory inside the image.

## Base image and system packages

`ubuntu:22.04` (pinned by digest) with `python3`, `tini`, `libgl1`, `libx11-6`, `libxrandr2`, `libxinerama1`,
`libfontconfig1`, `xvfb` (X.Org, MIT/X11), `libgl1-mesa-dri` (Mesa, MIT) and `ffmpeg`, plus the `uv` build tool in
an intermediate stage only. **Ubuntu's `ffmpeg` is built with `--enable-gpl` and `libx264`, so it is distributed
under the GPL-2.0-or-later.** The service only runs it as a separate program to encode videos; the service code
is not linked with it. These are distributed under their own
licences (mostly GPL, LGPL, MIT and BSD variants); the licence texts are in `/usr/share/doc/<package>/copyright` in
the image. The corresponding source is available from Ubuntu's package archive.
