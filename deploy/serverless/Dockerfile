# ─────────────────────────────────────────────────────────────────────────────
# RunPod Serverless image: ComfyUI + our serverless handler.
# Models live on a RunPod network volume (mounted at /runpod-volume) — NOT baked
# in — so the image stays small and cold starts stay fast (use FlashBoot).
#
# Build & push:
#   docker build -f deploy/serverless/Dockerfile -t <registry>/aivideo-serverless:latest .
#   docker push <registry>/aivideo-serverless:latest
# Then create a RunPod Serverless endpoint from that image (see docs/INTEGRATION.md).
# ─────────────────────────────────────────────────────────────────────────────
FROM runpod/pytorch:2.4.0-py3.12-cuda12.4.1-devel-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    COMFY_DIR=/opt/ComfyUI \
    APP_DIR=/opt/app

RUN apt-get update && apt-get install -y --no-install-recommends git curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── ComfyUI ──
ARG COMFYUI_REF=master
RUN git clone --depth 1 --branch ${COMFYUI_REF} https://github.com/comfyanonymous/ComfyUI ${COMFY_DIR} \
    && pip install -r ${COMFY_DIR}/requirements.txt

# ── Custom nodes your workflows need (extend this list as you add workflows) ──
RUN git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite \
        ${COMFY_DIR}/custom_nodes/ComfyUI-VideoHelperSuite \
    && pip install -r ${COMFY_DIR}/custom_nodes/ComfyUI-VideoHelperSuite/requirements.txt || true

# ── Our app (serverless handler + config) ──
WORKDIR ${APP_DIR}
COPY requirements.txt ./
RUN pip install -r requirements.txt runpod boto3

COPY app ./app
COPY config ./config
COPY deploy ./deploy
RUN chmod +x deploy/serverless/start.sh

# ComfyUI must find the volume config at runtime; models resolve from /runpod-volume.
CMD ["deploy/serverless/start.sh"]
