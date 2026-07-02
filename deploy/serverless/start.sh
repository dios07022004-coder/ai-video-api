#!/usr/bin/env bash
# Serverless container entrypoint.
#   1) launch ComfyUI (bound to localhost) in the background
#   2) wait until it answers
#   3) start the RunPod serverless handler (blocks)
#
# Models & custom nodes are expected on the RunPod **network volume**, mounted at
# /runpod-volume. extra_model_paths.yaml points ComfyUI there so you never bake
# multi-GB checkpoints into the image.
set -euo pipefail

COMFY_DIR="${COMFY_DIR:-/opt/ComfyUI}"
APP_DIR="${APP_DIR:-/opt/app}"
export PYTHONPATH="${APP_DIR}:${PYTHONPATH:-}"
export AIV_COMFY_HOST="127.0.0.1"
export AIV_COMFY_PORT="8188"

echo "[start] launching ComfyUI from ${COMFY_DIR}"
cd "${COMFY_DIR}"
python main.py \
  --listen 127.0.0.1 --port 8188 \
  --extra-model-paths-config "${APP_DIR}/deploy/serverless/extra_model_paths.yaml" \
  --disable-auto-launch --dont-print-server &
COMFY_PID=$!

echo "[start] waiting for ComfyUI…"
for i in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:8188/system_stats" >/dev/null 2>&1; then
    echo "[start] ComfyUI is up"
    break
  fi
  if ! kill -0 "${COMFY_PID}" 2>/dev/null; then
    echo "[start] ComfyUI died during boot" >&2; exit 1
  fi
  sleep 2
done

echo "[start] starting serverless handler"
cd "${APP_DIR}"
exec python -m app.serverless.main
