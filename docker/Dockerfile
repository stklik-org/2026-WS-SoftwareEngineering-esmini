# syntax=docker/dockerfile:1
#
# esmini REST service image (PLAN.md §7). Build from the repository root:
#   docker build --platform linux/amd64 -f docker/Dockerfile -t esmini-rest:dev .
#
# Stages: esmini-fetch (official release archive, checksum-verified) -> python-deps (locked venv) -> runtime.
# Only linux/amd64 is supported for now (PLAN.md D10): esmini publishes Linux binaries for x86-64 only.

ARG UBUNTU_IMAGE=ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.10.6@sha256:2f2ccd27bbf953ec7a9e3153a4563705e41c852a5e1912b438fc44d88d6cb52c

# --- esmini ------------------------------------------------------------------------------------------------------
FROM ${UBUNTU_IMAGE} AS esmini-fetch
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ARG TARGETARCH
ARG ESMINI_VERSION=3.8.1
# The "demo" archive contains the binaries plus resources (example scenarios, roads, catalogs, 3D models) and the
# third-party licence texts. Only the binaries and licence texts are kept. Checksum as published in the GitHub
# release metadata of v3.8.1.
ARG ESMINI_ARCHIVE=esmini-demo_Linux.zip
ARG ESMINI_ARCHIVE_SHA256=8bebf1b9bb72942ed08755c3a666c23b866b287a1846bba2bc3410ed740b6c59

RUN if [ "${TARGETARCH}" != "amd64" ]; then \
      echo "esmini release binaries exist for linux/amd64 only; build with --platform linux/amd64 (PLAN.md D10)." >&2; \
      exit 1; \
    fi
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL --retry 3 -o /tmp/esmini.zip \
      "https://github.com/esmini/esmini/releases/download/v${ESMINI_VERSION}/${ESMINI_ARCHIVE}" \
 && echo "${ESMINI_ARCHIVE_SHA256}  /tmp/esmini.zip" | sha256sum -c - \
 && unzip -q /tmp/esmini.zip -d /tmp/unpacked \
 && mv /tmp/unpacked/esmini-demo /opt/esmini \
 && grep -q "ESMINI_GIT_TAG=\"v${ESMINI_VERSION}\"" /opt/esmini/version.txt \
 # config.yml would silently add viewer options (window size) to every run; the service passes all options
 # explicitly instead. resources/ is removed so that a run uses only the uploaded files: esmini would otherwise
 # fall back to its sample roads and catalogs for any file the client forgot. The demo launch scripts, coding
 # example and C headers are not needed.
 && rm -rf /opt/esmini/config.yml /opt/esmini/resources /opt/esmini/run /opt/esmini/Hello-World_coding-example \
           /opt/esmini/EnvironmentSimulator /tmp/esmini.zip /tmp/unpacked

# --- Python dependencies -----------------------------------------------------------------------------------------
FROM ${UV_IMAGE} AS uv
FROM ${UBUNTU_IMAGE} AS python-deps
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime -----------------------------------------------------------------------------------------------------
FROM ${UBUNTU_IMAGE} AS runtime
ARG ESMINI_VERSION=3.8.1
ARG ESMINI_COMMIT=19d26b6
ARG IMAGE_VERSION=dev

# Shared libraries of esmini/replayer/odrviewer as reported by `ldd` (docs/spikes/01-release-archive.md); even the
# headless binaries link against GL/X11. Videos (docs/spikes/02-rendering.md) need a virtual X server (xvfb), Mesa's
# software OpenGL (libgl1-mesa-dri) and ffmpeg. tini reaps processes and forwards signals; prlimit (util-linux) is
# in the base image.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      python3 tini libgl1 libx11-6 libxrandr2 libxinerama1 libfontconfig1 \
      xvfb libgl1-mesa-dri ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 esmini \
 && useradd --uid 1000 --gid esmini --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin esmini \
 && mkdir -p /data/jobs /var/cache/matplotlib \
 && chown -R esmini:esmini /data /var/cache/matplotlib

COPY --from=esmini-fetch /opt/esmini /opt/esmini
COPY --from=python-deps /opt/venv /opt/venv
WORKDIR /srv
COPY app ./app
COPY LICENSE THIRD-PARTY-NOTICES.md /usr/share/doc/esmini-rest/

ENV PATH=/opt/venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ESMINI_HOME=/opt/esmini \
    DATA_DIR=/data \
    PORT=8080 \
    MPLCONFIGDIR=/var/cache/matplotlib \
    IMAGE_VERSION=${IMAGE_VERSION}

LABEL org.opencontainers.image.title="esmini-rest" \
      org.opencontainers.image.description="esmini OpenSCENARIO simulations behind a REST API" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.licenses="MIT AND MPL-2.0" \
      io.github.esmini.version="${ESMINI_VERSION}" \
      io.github.esmini.commit="${ESMINI_COMMIT}"

USER esmini
# Build matplotlib's font cache now; otherwise the first road preview spends seconds on it.
RUN python3 -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot"
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=2)"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "app"]
