# Architecture

## 1. High-level

Two servers. The website (Server #1) is a **thin partner** that only speaks the
public REST contract. Everything AI lives on the RunPod GPU Pod (Server #2).

```
                        ┌───────────────────────── RunPod GPU Pod ──────────────────────────┐
  Partner backend       │                                                                    │
  ┌───────────┐  HTTPS  │  ┌─────────┐   ┌───────────────────────┐   ┌──────────────────┐    │
  │  website  │────────▶│  │  NGINX  │──▶│      FastAPI (API)     │──▶│  Redis (RQ queue)│    │
  │           │◀────────│  └─────────┘   │  routers → services →  │   └────────┬─────────┘    │
  └───────────┘ callback│                │  repositories → DB     │            │              │
        ▲               │                └───────────┬───────────┘            ▼              │
        │               │                            │              ┌──────────────────┐     │
        │  POST result  │                            ▼              │   RQ Worker(s)    │     │
        └───────────────│                   SQLite / PostgreSQL     │  generation      │     │
                        │                                           │  pipeline        │     │
                        │                                           └────────┬─────────┘     │
                        │                                                    │ HTTP + WS     │
                        │                                           ┌────────▼─────────┐     │
                        │                                           │     ComfyUI      │     │
                        │                                           └────────┬─────────┘     │
                        │                                     input/ ◀───────┴──────▶ output/ │
                        │                                     (uploads)          (results)    │
                        └────────────────────────────────────────────────────────────────────┘
```

## 2. Request lifecycles

### Upload
`POST /uploads` → validate MIME/size/pixels/decode → strip to safe re-encode →
UUID filename under `uploads/YYYY/MM/DD/` → also placed into ComfyUI `input/` →
return absolute `image_url`.

### Generation (async, never blocks)
```
POST /generate
  → auth (API key → partner)
  → idempotency check on (partner, request_id)   ── reuse if seen
  → resolve mode + workflow + model from registry
  → validate/merge params against mode schema
  → price = mode.price_credits; reserve/hold credits
  → persist Task(status=queued)
  → enqueue RQ job(task_id)
  → 202 { task_id, status:"queued", idempotent_reuse:false }

Worker(job)
  → Task→loading
  → build parameter set (defaults ← mode ← request ← safety clamps)
  → WorkflowEngine.render(workflow_json, params)   # {{PLACEHOLDER}} injection
  → Task→preparing
  → ComfyClient.submit(prompt)                      # /prompt
  → Task→running ; subscribe WS progress → Task.progress
  → Task→encoding (video node executing)
  → download artifact from ComfyUI output
  → StorageService.persist → results/YYYY/MM/DD/<uuid>.mp4
  → Task→completed(result_url) ; commit credit ledger
  → CallbackService.fire(callback_url)   # retry w/ backoff + HMAC
  on error: Task→failed(error) ; refund hold ; callback(failed)
```

### Poll
`GET /tasks/{id}` returns normalized status, progress %, result_url, error, price.

## 3. Layered design (SOLID)

| Layer          | Responsibility                                         | Depends on |
|----------------|--------------------------------------------------------|------------|
| **routers**    | HTTP shape, status codes, DI wiring                    | services, schemas |
| **services**   | business rules, orchestration, transactions            | repositories, registries, comfy, storage |
| **repositories** | persistence only (no business logic)                 | database |
| **registries** | in-memory hot-reloadable config (modes/workflows/models)| config files |
| **comfy**      | ComfyUI protocol (submit, poll, progress, download)    | httpx/websockets |
| **workflow engine** | placeholder resolution + graph validation         | — (pure) |
| **storage**    | filesystem abstraction, dated paths, cleanup           | aiofiles |
| **queue/workers** | async decoupling + the generation pipeline          | services |
| **security**   | auth, file validation, sanitization, rate limiting     | — |

Dependencies point **inward**. Swapping SQLite→Postgres, local→S3, or RQ→Celery
changes exactly one adapter and no callers (Dependency Inversion).

## 4. Extensibility contract

`RegistryService` watches `config/` and exposes typed views. A mode JSON is the
single source of truth binding **mode → workflow → model → params → price**.
The engine never imports a mode by name; it looks it up. New models/LoRAs/VAEs are
just rows in `models.json` referenced by a workflow placeholder value.

## 5. Scalability path

- **Multiple GPUs / Pods**: `ComfyClient` is constructed per-worker from a pool of
  ComfyUI endpoints (`AIV_COMFY_ENDPOINTS`), so N workers → N GPUs. Redis is the
  shared distributed queue.
- **Shared storage**: `StorageBackend` interface → swap `LocalStorage` for
  `S3Storage`/NFS without touching services.
- **Load balancing**: NGINX in front of API replicas; workers scale independently.
- **DB**: SQLite → Postgres by env var only (SQLAlchemy async engine).

## 6. Error model

Every failure returns:
```json
{ "success": false, "error": { "code": "MODEL_NOT_FOUND", "message": "…", "details": {} } }
```
Python exceptions are never leaked. `AppError` subclasses carry `code`, HTTP status
and safe `details`; a global handler renders them. Validation errors keep FastAPI's
422 body for tooling compatibility.
