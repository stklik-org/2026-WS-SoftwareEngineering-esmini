#!/usr/bin/env bash
# Manual smoke test of a running service with curl: health, info, one simulation (submit, poll, result),
# artefact downloads, a video, a road preview, and a few error cases. Usage: scripts/smoke.sh [base-url]   (default http://127.0.0.1:8080)
# Downloads go to ./smoke-output/.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8080}"
DATA="$(cd "$(dirname "$0")/.." && pwd)/tests/data"
OUT="smoke-output"
mkdir -p "$OUT"

json() { python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "GET /health"
curl -fsS "$BASE/health"; echo

step "GET /v1/info"
curl -fsS "$BASE/v1/info" | json '"esmini %s (%s), arch %s, image %s" % (d["esmini"]["version"], d["esmini"]["gitRevision"], d["arch"], d["imageVersion"])'

# Submit a run and wait for it: prints the job's result JSON. Usage: run <scenario> <files...> -- <curl args...>
run() {
  local scenario="$1"; shift
  local args=(-F "scenario=$scenario")
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do args+=(-F "files=@$DATA/$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  local job state
  job=$(curl -fsS -X POST "$BASE/v1/simulations" "${args[@]}" "$@" | json 'd["jobId"]')
  while state=$(curl -fsS "$BASE/v1/jobs/$job/status" | json 'd["state"]'); [ "$state" != "finished" ]; do
    sleep 0.5
  done
  curl -fsS "$BASE/v1/jobs/$job"
}

step "POST /v1/simulations  (cut-in.xosc + e6mini.xodr + VehicleCatalog.xosc), poll status, fetch result"
run cut-in.xosc cut-in.xosc e6mini.xodr VehicleCatalog.xosc -- \
  -F 'config={"timestep": 0.05, "outputs": ["dat", "csv", "log"]}' >"$OUT/result.json"
python3 -m json.tool "$OUT/result.json"

step "Download artefacts to $OUT/"
for url in $(json '"\n".join(a["url"] for a in d["artifacts"])' <"$OUT/result.json"); do
  name="${url##*/}"
  curl -fsS "$BASE$url" -o "$OUT/$name"
  printf '%-8s %8s bytes\n' "$name" "$(wc -c <"$OUT/$name" | tr -d ' ')"
done
echo "First CSV rows:"; head -3 "$OUT/sim.csv"

step "Video: cut-in.xosc at 640x360, top camera -> $OUT/video.mp4"
run cut-in.xosc cut-in.xosc e6mini.xodr VehicleCatalog.xosc -- \
  -F 'config={"outputs": ["video"], "video": {"width": 640, "height": 360, "cameraMode": "top"}}' >"$OUT/video.json"
url=$(json 'next(a["url"] for a in d["artifacts"] if a["name"] == "video.mp4")' <"$OUT/video.json")
curl -fsS "$BASE$url" -o "$OUT/video.mp4"
printf 'video.mp4 %8s bytes\n' "$(wc -c <"$OUT/video.mp4" | tr -d ' ')"

step "POST /v1/road-previews  (e6mini.xodr) -> $OUT/e6mini.png"
curl -fsS -X POST "$BASE/v1/road-previews" -F "road=@$DATA/e6mini.xodr" -o "$OUT/e6mini.png" \
  -w 'HTTP %{http_code}, %{size_download} bytes, %{time_total} s\n'

step "Error case: unknown config field -> 422"
curl -sS -X POST "$BASE/v1/simulations" -F "files=@$DATA/cut-in.xosc" -F scenario=cut-in.xosc \
  -F 'config={"stopTime": 10}' -w '\nHTTP %{http_code}\n'

step "Error case: catalog not uploaded -> status failed (the image has no esmini resources)"
run cut-in.xosc cut-in.xosc e6mini.xodr | json '"status=%s exitCode=%s" % (d["status"], d["exitCode"])'

step "Error case: endless scenario with a 3 s limit -> status timed_out"
run endless.xosc endless.xosc e6mini.xodr VehicleCatalog.xosc -- -F 'config={"limits": {"wallTimeoutSeconds": 3}}' \
  | json '"status=%s wallSeconds=%s" % (d["status"], d["wallSeconds"])'

printf '\n\033[1mSmoke test finished.\033[0m Swagger UI: %s/docs\n' "$BASE"
