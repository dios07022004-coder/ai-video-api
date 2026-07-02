# AI Video Generation API

Production-grade abstraction layer over **ComfyUI** running on **RunPod GPU Pods**.

Your website (Server #1) talks HTTP to this API (Server #2). The API owns ComfyUI,
the task queue, storage, billing and workflow orchestration. The website never sees
ComfyUI internals.

```
┌──────────────┐      HTTPS       ┌──────────────────────────────────────────────┐
│  Website /   │ ───────────────▶ │  RunPod GPU Pod (this project)                │
│  Partner     │                  │                                               │
│  backend     │ ◀─── callback ── │  NGINX ─▶ FastAPI (API)  ─▶ Redis Queue        │
└──────────────┘                  │                    │            │             │
                                  │                    ▼            ▼             │
                                  │              SQLite/Postgres  RQ Worker ─▶ ComfyUI
                                  │                                   │           │
                                  │                              results/ uploads/│
                                  └──────────────────────────────────────────────┘
```

## Why this is modular

Adding a new capability **never touches Python code**. You only drop in files:

| To add a…      | You create…                                             |
|----------------|---------------------------------------------------------|
| Mode           | `config/modes/<id>.json`                                |
| Workflow       | `config/workflows/<name>.json` (ComfyUI API-format)     |
| Model          | an entry in `config/models.json`                        |
| LoRA / VAE / ControlNet / IPAdapter | an entry in `config/models.json` + reference from a mode/workflow |
| Sampler / Scheduler | allowed value in a mode's `params` schema          |
| Preview        | `static/previews/<id>.jpg`                              |

Then `POST /admin/modes/reload` (or restart) — no deploy, no code change.

## Placeholder contract

Workflows are stored as ComfyUI **API-format JSON** containing `{{PLACEHOLDER}}`
tokens. The Workflow Engine replaces them from the resolved parameter set before
submission. Supported tokens include:

```
{{IMAGE}} {{PROMPT}} {{NEGATIVE}} {{WIDTH}} {{HEIGHT}} {{SEED}} {{CFG}}
{{STEPS}} {{SAMPLER}} {{SCHEDULER}} {{FPS}} {{FRAMES}} {{MODEL}} {{LORA}}
{{CONTROL_VIDEO}} {{DENOISE}} {{BATCH}} … (extensible — any {{UPPER_SNAKE}})
```

Node IDs are **never** hardcoded. Placeholders are resolved by textual/structural
substitution across the whole graph, so a workflow author is free to reorganize
nodes at will.

## Quick start (local dev)

```bash
cp .env.example .env
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e .[dev]
# terminal 1 — API
uvicorn app.main:app --reload --port 8000
# terminal 2 — worker (needs Redis; on Windows use docker or WSL)
python -m app.workers.worker
```

Open http://localhost:8000/docs

## Two ways to run on RunPod

1. **RunPod Serverless (recommended for a website with users — cheapest).**
   Scales to zero, bills per generation second. Your existing site/API stays the
   source of truth for users/credits and just calls the endpoint over REST.
   Full guide + client code: [`docs/INTEGRATION.md`](docs/INTEGRATION.md).
   Image: [`deploy/serverless/Dockerfile`](deploy/serverless/Dockerfile).

   Test the handler locally (needs a reachable ComfyUI):
   ```bash
   pip install -e .[serverless]
   python -m app.serverless.main   # or: python handler test with deploy/serverless/test_input.json
   ```

2. **Persistent GPU pod (full FastAPI + queue).** Best only for near-constant
   load. See [`docs/DEPLOY_RUNPOD.md`](docs/DEPLOY_RUNPOD.md):
   ```bash
   docker compose up --build
   ```
   The container auto-detects ComfyUI, GPU and VRAM and exposes `/health`.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.
