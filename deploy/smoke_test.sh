#!/usr/bin/env bash
set -euo pipefail

source /opt/nerfstudio-api/.env
API_URL=${NERFSTUDIO_URL:-https://34.143.145.140.nip.io}
SMOKE_ROOT=/tmp/ns-smoke
OUTPUT_TYPE=${OUTPUT_TYPE:-model}
AUTH_HEADERS=()
if [[ "${API_AUTH_REQUIRED:-false}" == "true" && -n "${API_KEY:-}" ]]; then
  AUTH_HEADERS=(-H "Authorization: Bearer ${API_KEY}")
fi

python3 - <<'PY'
import shutil
shutil.make_archive('/tmp/ns-smoke/dataset', 'zip', '/tmp/ns-smoke/poster')
PY

curl --fail --silent --show-error \
  "${AUTH_HEADERS[@]}" \
  -F "dataset=@${SMOKE_ROOT}/dataset.zip" \
  -F "method=nerfacto" \
  -F "iterations=100" \
  -F "output_type=${OUTPUT_TYPE}" \
  "${API_URL}/v1/jobs" > "${SMOKE_ROOT}/response.json"

JOB_ID="$(python3 -c 'import json; print(json.load(open("/tmp/ns-smoke/response.json"))["id"])')"
echo "JOB_ID=${JOB_ID}"

for ignored in $(seq 1 60); do
  curl --fail --silent --show-error \
    "${AUTH_HEADERS[@]}" \
    "${API_URL}/v1/jobs/${JOB_ID}" > "${SMOKE_ROOT}/status.json"
  STATUS="$(python3 -c 'import json; print(json.load(open("/tmp/ns-smoke/status.json"))["status"])')"
  echo "STATUS=${STATUS}"
  if [[ "${STATUS}" == "complete" ]]; then
    curl --fail --silent --show-error \
      "${AUTH_HEADERS[@]}" \
      -o "${SMOKE_ROOT}/result.zip" \
      "${API_URL}/v1/jobs/${JOB_ID}/result"
    test -s "${SMOKE_ROOT}/result.zip"
    echo "RESULT_BYTES=$(stat -c %s "${SMOKE_ROOT}/result.zip")"
    exit 0
  fi
  if [[ "${STATUS}" == "failed" ]]; then
    cat "${SMOKE_ROOT}/status.json"
    exit 1
  fi
  sleep 5
done

echo "Timed out waiting for smoke job"
exit 1
