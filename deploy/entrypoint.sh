#!/usr/bin/env bash
# Container entrypoint. Modes:
#   api    → run FastAPI (uvicorn)               [default]
#   worker → run the RQ generation worker
#   all    → run api + worker under supervisord  (single-pod RunPod deploy)
#   bash   → drop into a shell
set -euo pipefail

MODE="${1:-api}"
export PYTHONPATH="/workspace/api:${PYTHONPATH:-}"

echo "[entrypoint] starting mode=${MODE}"

# Ensure runtime directories + DB schema exist before anything serves traffic.
python -m scripts.manage init-db || true

case "${MODE}" in
  api)
    exec uvicorn app.main:app --host "${AIV_API_HOST:-0.0.0.0}" --port "${AIV_API_PORT:-8000}"
    ;;
  worker)
    exec python -m app.workers.worker
    ;;
  all)
    exec supervisord -c /workspace/api/deploy/supervisord.conf
    ;;
  bash)
    exec /bin/bash
    ;;
  *)
    echo "[entrypoint] unknown mode: ${MODE}" >&2
    exit 64
    ;;
esac
