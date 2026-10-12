# esmini REST service

A Docker image that runs [esmini](https://github.com/esmini/esmini) **v3.8.1** OpenSCENARIO simulations behind a
REST API with interactive Swagger documentation. Students of the AV Sim theme (PR Software Engineering, JKU,
WS 2026/27) run the container on their own machine and call it from their Java applications. The full specification
is in [`PLAN.md`](PLAN.md).

**Status: iteration 1.** The image, asynchronous headless simulation runs with DAT/CSV/log artefacts, videos and
road previews are in place. Snapshots, permutations, parameter overrides and the documentation site follow (see
`GET /v1/info` → `capabilities`).

## Quick start

Requirements: Docker (Docker Desktop on macOS/Windows). The image is `linux/amd64`. On Apple-silicon Macs,
Docker runs it under emulation. Enable *Settings → General → Use Rosetta for x86_64/amd64 emulation on Apple
Silicon* for speed.

The image is published on the GitHub Container Registry:
[`ghcr.io/stklik-org/esmini-rest`](https://github.com/orgs/stklik-org/packages/container/package/esmini-rest)
(repository: [stklik-org/2026-WS-SoftwareEngineering-esmini](https://github.com/stklik-org/2026-WS-SoftwareEngineering-esmini)).
No login and no clone of this repository are needed. One command downloads the newest image and starts it:

```sh
docker run --rm --pull always --platform linux/amd64 -p 127.0.0.1:8080:8080 ghcr.io/stklik-org/esmini-rest:latest
```

Then open the Swagger UI at <http://localhost:8080/docs> ("Try it out"). Ctrl+C stops the container.

- `--pull always` checks the registry on every start and downloads a newer image if there is one; without it,
  Docker keeps using the copy it downloaded first.
- The port is published on `127.0.0.1` only, so the service cannot be reached from other machines.
- Add `-d --name esmini` to run it in the background (`docker stop esmini` stops it), and `-e NAME=value` for the
  settings under [Configuration of the container](#configuration-of-the-container).

| Tag | Content |
|---|---|
| `latest` | The newest release |
| `0.1.0`, `0.1`, ... | A fixed release (from 1.0.0 on also the major version, `1`). Pin one so that your team keeps the same image throughout the course. |
| `esmini-3.8.1` | The newest release built with this esmini version |
| `main` | Pre-release builds for the instructors |

`GET /v1/info` reports the version of the running image (`imageVersion`) and of esmini.

From a clone of this repository, Docker Compose starts the published image as well:

```sh
ESMINI_IMAGE=ghcr.io/stklik-org/esmini-rest:latest docker compose up -d --wait
docker compose down
```

## Using the API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness: `{"status": "ok"}` |
| GET | `/v1/info` | esmini version, architecture, capabilities and server limits |
| POST | `/v1/simulations` | Submit a scenario run (multipart upload); answers `202` at once |
| GET | `/v1/jobs` | All jobs the service still holds, whatever their state, newest first |
| GET | `/v1/jobs/{jobId}/status` | `queued`, `running`, `finished` or `interrupted` (service restarted before the job finished) |
| GET | `/v1/jobs/{jobId}` | The result of a finished job (`409` while it is queued or running) |
| GET | `/v1/jobs/{jobId}/artifacts` | List the artefacts of a job |
| GET | `/v1/jobs/{jobId}/artifacts/{name}` | Download `sim.dat`, `sim.csv`, `log.txt` or `video.mp4` |
| POST | `/v1/road-previews` | Draw an OpenDRIVE road network as a PNG (answered at once, no job) |

```sh
curl -X POST http://localhost:8080/v1/simulations \
  -F files=@tests/data/cut-in.xosc \
  -F files=@tests/data/e6mini.xodr \
  -F files=@tests/data/VehicleCatalog.xosc \
  -F scenario=cut-in.xosc \
  -F 'config={"timestep": 0.05, "outputs": ["dat", "csv", "log"], "limits": {"wallTimeoutSeconds": 60}}'
# 202 {"jobId": "...", "state": "queued", "statusUrl": "/v1/jobs/.../status", "resultUrl": "/v1/jobs/..."}

curl http://localhost:8080/v1/jobs/<jobId>/status    # repeat until "state": "finished"
curl http://localhost:8080/v1/jobs/<jobId>           # the result, with artefact URLs
```

The multipart request has these parts:

- `files` (required, repeatable): the scenario and **every** file it refers to: roads, catalogs, SUMO files, 3D
  models (`.osgb`), textures (`.jpg`, `.png`) and any other data. Any file type is accepted (esmini only reads
  these files; it loads no code from a scenario). XML files (`.xosc`, `.xodr`, `.xml`, `.sumocfg`) are checked.
- `scenario` (required): the file name of the scenario to run, which must be one of `files` and end in `.xosc`.
- `config` (optional): JSON text. `-F 'config=<config.json'` and `-F config=@config.json` both work.

All files are stored flat in one directory, which is esmini's working directory. A reference such as
`../xodr/e6mini.xodr` or a catalog directory `../xosc/Catalogs/Vehicles` therefore resolves by file name among the
uploads. The image contains **no** esmini sample scenarios, roads, catalogs or 3D models: a file that is referred
to but not uploaded makes the run fail. 3D models and textures are only needed for videos; without them vehicles
and road objects appear as boxes. One kind of file cannot be supplied: esmini looks for traffic-signal textures
only in a sub-folder (`<map dir>/../models/roads_signal_textures/`), and uploads are flat, so such signals stay
boxes.

The request is checked before the job is created, so errors in it (invalid config, file name or XML, scenario not
among the files, sizes) return `4xx` with `{"code", "message", "details"}`, and a full queue returns `429` with
`Retry-After`. An accepted job is `queued` until an esmini slot is free, then `running`, then `finished`.

`config` (all fields optional; unknown fields are rejected with 422):

| Field | Default | esmini flag |
|---|---|---|
| `timestep` | `0.05` | `--fixed_timestep` |
| `collisionDetection` | `true` | `--collision` (omitted with `false`) |
| `disableControllers` | `false` | `--disable_controllers` |
| `seed` | esmini default | `--seed` |
| `logLevel` | `"info"` | `--log_level` |
| `outputs` | `["dat", "csv", "log"]` | `--record`; CSV via `dat2csv --extended`; add `"video"` for `video.mp4` |
| `video.width`, `video.height` | `1280`, `720` (max 1920×1080, even) | replayer `--window` |
| `video.framesPerSecond` | `20` | – (frames are resampled; the video plays in real time) |
| `video.cameraMode` | `"orbit"` (`fixed`, `flex`, `flex-orbit`, `top`, `driver`) | replayer `--camera_mode` |
| `limits.wallTimeoutSeconds` | `60` (server maximum 300) | – (esmini has no time limit; the service kills the run) |

### Videos

With `"outputs": [..., "video"]` the recording is replayed in esmini's 3D viewer off-screen (Xvfb, software
OpenGL) and encoded as H.264 MP4. Upload the 3D models (`.osgb`) the scenario names, esmini's defaults
(`car_white.osgb`, `car_red.osgb`, `cyclist.osgb`, `mc.osgb`, `walkman.osgb`) and the road textures (`asphalt.jpg`,
`grass.jpg`) with the scenario; anything missing is drawn as a box or in flat colours.
esmini renders one image per simulation step, so rendering time grows with the number of steps: under Rosetta
the 22 s `cut-in.xosc` takes about 20 s at 1280×720. Use a coarser `timestep` or a smaller size for long
scenarios. A video covers at most `maxVideoSeconds` of simulation time (default 120 s) and must render within
`videoTimeoutSeconds` (300 s). A failed video does not fail the run; `warnings` says why.

```sh
curl -X POST http://localhost:8080/v1/simulations \
  -F files=@tests/data/cut-in.xosc -F files=@tests/data/e6mini.xodr -F files=@tests/data/VehicleCatalog.xosc \
  -F scenario=cut-in.xosc \
  -F 'config={"outputs": ["csv", "video"], "video": {"width": 1280, "height": 720, "cameraMode": "top"}}'
```

### Road previews

```sh
curl -X POST http://localhost:8080/v1/road-previews -F road=@tests/data/e6mini.xodr \
  -F 'config={"renderer": "odrviewer", "width": 1200, "height": 900}' -o e6mini.png
```

The response is the PNG itself, a top view of the whole road network. `config` (optional):

- `renderer`: `odrviewer` (default) shows the network in esmini's 3D viewer, straight from above (x to the right,
  y up): road surfaces, markings and road objects, in flat colours because the image has no textures. `plot`
  draws every lane border (reference lines red, driving lanes blue, others grey) with axes in metres.
- `width`, `height` (pixels, 200–4000), `roadIds` (`plot` only), `samplingStep` (metres, 0.1–10; `odrviewer`
  uses the samples only to frame the picture).

### Errors

A run that fails or times out still finishes; its result has `status` `failed` or `timed_out`, and the
`warnings` and `log.txt` explain why. Results and artefacts expire after `JOB_TTL_SECONDS`.

## Configuration of the container

Environment variables: `MAX_CONCURRENT_JOBS` (default: number of CPUs), `MAX_QUEUED_JOBS` (16),
`JOB_TTL_SECONDS` (3600), `MAX_UPLOAD_MB` (64 per file; environment models reach about 30 MB), `MAX_TOTAL_UPLOAD_MB` (128), `DEFAULT_TIMEOUT_S` (60),
`MAX_TIMEOUT_S` (300), `MAX_OUTPUT_MB` (500 per output file), `MAX_VIDEO_SECONDS` (120), `VIDEO_TIMEOUT_S`
(300), `PREVIEW_TIMEOUT_S` (60, per step of a road preview), `CORS_ORIGINS` (default: `localhost` and
`127.0.0.1` on any port), `LOG_LEVEL` (`info`), `PORT` (8080), `DATA_DIR` (`/data`).

## Development

Tools: Docker, [uv](https://docs.astral.sh/uv/) and make. `uv sync` creates `.venv` with Python 3.10, the Python
version of the container.

To build and run the image from source instead of pulling it:

```sh
docker compose up -d --build --wait      # or: make up (image esmini-rest:dev)
docker compose down                      # or: make down
# without Compose:
docker build --platform linux/amd64 -f docker/Dockerfile -t esmini-rest:dev .
docker run --rm --platform linux/amd64 -p 127.0.0.1:8080:8080 esmini-rest:dev
```

| Command | What it does |
|---|---|
| `make test` | ruff, then unit and contract tests (no Docker; esmini is replaced by a fake) |
| `make test-integration` | builds the image, starts a test container on port 18080 with `MAX_CONCURRENT_JOBS=2 MAX_QUEUED_JOBS=2`, runs the acceptance tests against the real esmini, stops the container |
| `make up` / `make smoke` / `make down` | start the service, run the curl smoke test (`scripts/smoke.sh`, downloads into `smoke-output/`), stop it |
| `make openapi` | regenerate `docs/openapi.json` (a contract test fails if it is stale) |
| `make help-texts` | capture the esmini `--help` texts from the image into `docs/reference/` |

A single test: `uv run pytest tests/unit/test_api.py::test_timeout_kills_the_run`. A single integration test
against a running service: `ESMINI_URL=http://127.0.0.1:8080 uv run pytest -m integration -k at3`.

On macOS, `make` can fail with "You have not agreed to the Xcode license agreements". Either accept the licence
(`sudo xcodebuild -license`) or use the Command Line Tools: `export DEVELOPER_DIR=/Library/Developer/CommandLineTools`.

### Releasing

`.github/workflows/build-image.yml` publishes the image to `ghcr.io/<owner>/esmini-rest` with the workflow's
`GITHUB_TOKEN` (no stored credentials). It runs the test workflow, builds the image, smoke-tests it
(`scripts/smoke.sh`) and pushes it only if all of that passes.

```sh
git tag v0.1.0 && git push origin v0.1.0   # -> :0.1.0, :0.1, :latest, :esmini-3.8.1, :sha-<commit>
```

A tag such as `v0.2.0-rc.1` publishes only `:0.2.0-rc.1`. *Actions → build-image → Run workflow* on `main`
publishes `:main` for a try-out before tagging. After the first push, set the package's visibility to *Public*
(organisation → *Packages* → `esmini-rest` → *Package settings*), otherwise students need a login to pull it. The
image is a single-platform manifest without provenance attestation (`docs/spikes/03-ghcr-publishing.md`).

Layout:

- `app/`: the service. `api/` has the routers, `core/` settings, errors, uploads, jobs and middleware, `esmini/`
  the argument builder, runner and output parsing, `services/` the simulation use case and `models/` the Pydantic
  schemas.
- `tests/`: `unit/` (with a fake esmini), `contract/` (flags against the help texts, OpenAPI drift),
  `integration/` (against the container) and `data/` (fixtures).
- `docker/Dockerfile`: multi-stage build. The official esmini release archive is downloaded and verified by sha256.
- `docs/`: `openapi.json`, `reference/` (esmini help texts) and `spikes/` (feasibility results and measurements).

## Licences

The service code is licensed under the MIT Licence (`LICENSE`). esmini and the esmini example files in
`tests/data/` are licensed under the Mozilla Public License 2.0. The image contains the esmini v3.8.1 release
archive without its `resources/` (sample content and 3D models), including its `LICENSE` and
`3rd_party_terms_and_licenses/` in `/opt/esmini`. See `THIRD-PARTY-NOTICES.md`.
