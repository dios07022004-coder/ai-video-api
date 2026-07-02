# Deploying on RunPod

This service is designed to run **inside the same GPU pod as ComfyUI**, or beside
it. It orchestrates ComfyUI over localhost and exposes the public REST API.

## Topology

```
RunPod GPU Pod
├── ComfyUI            :8188   (from the RunPod ComfyUI template)
├── Redis              :6379   (apt/docker, or a managed Redis)
├── AI Video API       :8000   (this project — supervisord runs api + worker)
└── NGINX              :80/443 (reverse proxy + static /files)
```

## 1. Prerequisites in the pod

- ComfyUI running and reachable at `127.0.0.1:8188` with the custom nodes your
  workflows use (e.g. `ComfyUI-VideoHelperSuite` for `VHS_VideoCombine`).
- Model files present in ComfyUI's model directories, named exactly as in
  `config/models.json` (`path` field).
- Redis available (or set `AIV_REDIS_URL` to a managed instance).

## 2. Configure

```bash
cp .env.example .env
# minimum edits:
#   AIV_PUBLIC_BASE_URL=https://<your-pod-id>-8000.proxy.runpod.net
#   AIV_ADMIN_TOKEN=<strong-random>
#   AIV_CALLBACK_HMAC_SECRET=<strong-random>
#   AIV_COMFY_HOST=127.0.0.1
#   AIV_COMFY_OUTPUT_DIR=/workspace/ComfyUI/output
```

## 3. Run (single container, api + worker)

```bash
docker build -t aivideo-api .
docker run -d --name aivideo \
  --env-file .env \
  -p 8000:8000 \
  -v /workspace/api-data:/workspace/api/data \
  -v /workspace/api-config:/workspace/api/config \
  aivideo-api all           # 'all' → supervisord runs api + worker
```

Or without Docker (RunPod shell):

```bash
pip install -r requirements.txt
python -m scripts.manage init-db
# terminal 1
uvicorn app.main:app --host 0.0.0.0 --port 8000
# terminal 2
python -m app.workers.worker
```

## 4. Provision a partner + credits

```bash
python -m scripts.manage create-partner --name "My Website" --credits 100000
# → prints partner_id and a one-time API key
```

## 5. Smoke test

```bash
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/modes -H "X-API-Key: <key>" | jq
```

## Multi-GPU / multi-pod scaling

- **More GPUs in one pod**: raise `numprocs` for `worker` in
  `deploy/supervisord.conf` and list every ComfyUI in `AIV_COMFY_ENDPOINTS`
  (e.g. `127.0.0.1:8188,127.0.0.1:8189`). `EndpointPool` load-balances by live
  queue depth.
- **More pods**: run the API once behind a load balancer, run workers on each GPU
  pod pointing at a shared Redis + shared storage (swap `LocalStorage` for an S3
  backend). No code change — only config.

## Graceful shutdown

Workers trap `SIGTERM`/`SIGINT` and finish the in-flight job (up to
`AIV_JOB_TIMEOUT_SECONDS`) before exiting. The API lifespan disposes the DB pool
and cancels the ComfyUI probe. RunPod's stop signal is handled cleanly.
