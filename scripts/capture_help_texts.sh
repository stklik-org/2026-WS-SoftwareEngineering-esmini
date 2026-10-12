#!/usr/bin/env bash
# Writes the --help texts of the esmini tools inside the image to docs/reference/. These files are the local
# source of truth for command-line flags (PLAN.md §6.4); tests/contract/test_flags.py checks every flag the
# service emits against them. Run after changing the esmini version: `make help-texts`.
set -euo pipefail

IMAGE="${1:-esmini-rest:dev}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/docs/reference"
mkdir -p "$OUT"

for tool in esmini replayer odrviewer dat2csv; do
  # The tools exit non-zero after printing help; the text itself is what matters.
  docker run --rm --platform linux/amd64 --entrypoint "/opt/esmini/bin/$tool" "$IMAGE" --help >"$OUT/$tool-help.txt" 2>&1 || true
  echo "wrote $OUT/$tool-help.txt ($(wc -l <"$OUT/$tool-help.txt") lines)"
done
# odrplot has no --help; it prints its usage when called without arguments.
docker run --rm --platform linux/amd64 --entrypoint /opt/esmini/bin/odrplot "$IMAGE" >"$OUT/odrplot-help.txt" 2>&1 || true
echo "wrote $OUT/odrplot-help.txt"
docker run --rm --platform linux/amd64 --entrypoint cat "$IMAGE" /opt/esmini/version.txt >"$OUT/esmini-version.txt"
echo "wrote $OUT/esmini-version.txt"
