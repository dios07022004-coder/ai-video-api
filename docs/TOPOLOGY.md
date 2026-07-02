# Топология из 3 серверов (Сайт → API на Selectel → RunPod)

Так это работает в проде под твою задачу. API из этого репозитория ставится на
**Сервер 2 (Selectel)** в режиме `AIV_GENERATION_BACKEND=runpod` — он не запускает
ComfyUI, а делегирует генерацию на **Сервер 3 (RunPod Serverless)** и получает
результат обратно по webhook.

```
┌────────────┐        ┌──────────────────────────────┐        ┌───────────────────────┐
│ Сервер 1   │        │ Сервер 2: API (Selectel)     │        │ Сервер 3: RunPod       │
│ САЙТ       │        │ FastAPI + Postgres (+Redis)  │        │ Serverless (ComfyUI)   │
│            │        │ AIV_GENERATION_BACKEND=runpod│        │                        │
│ 1. POST ───┼───────▶│ /generate                    │        │                        │
│    /generate        │  ├ проверка кредитов + hold   │        │                        │
│            │        │  ├ POST /run ────────────────┼───────▶│ ComfyUI обрабатывает   │
│ 2. task_id ◀────────┤  └ вернул task_id (202)      │        │  модели с volume       │
│            │        │                              │        │  результат → S3/R2     │
│ 3. GET ────┼───────▶│ /tasks/{id}  (опрос статуса) │        │        │               │
│    /tasks/{id}      │                              │◀───────┼── webhook (готово)     │
│ 4. видео ◀──────────┤  /runpod/webhook/{secret}    │        │                        │
│  (ссылка из S3)     │   ├ charge / refund          │        └───────────────────────┘
└────────────┘        │   └ callback на сайт (опц.)   │
                      └──────────────────────────────┘
```

## Что на каком сервере

| | Сервер 1 (Сайт) | Сервер 2 (API, Selectel) | Сервер 3 (RunPod) |
|---|---|---|---|
| Роль | UI + твой бэкенд | оркестратор, кредиты, задачи | GPU-генерация |
| Из репо | — | весь `app/` (backend=runpod) | `deploy/serverless/*` |
| GPU | нет | нет | да (до 0 в простое) |
| Включён | 24/7 | **24/7** | по требованию |
| Хранит видео | нет | только ссылку | заливает в S3/R2 |

## Минимальная конфигурация Сервера 2 (Selectel)

`.env`:
```
AIV_GENERATION_BACKEND=runpod
AIV_PUBLIC_BASE_URL=https://api.твойдомен.com     # публичный HTTPS обязателен для webhook
AIV_RUNPOD_API_KEY=<runpod api key>
AIV_RUNPOD_ENDPOINT_ID=<endpoint id>
AIV_RUNPOD_WEBHOOK_SECRET=<длинный секрет>
AIV_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/aivideo
# видео отдаём из объектного хранилища Selectel (S3-совместимое):
AIV_S3_BUCKET=...  AIV_S3_ENDPOINT=https://s3.ru-1.storage.selcloud.ru  AIV_S3_ACCESS_KEY=... AIV_S3_SECRET_KEY=...
```

Запуск (только API, без воркера — RunPod сам себе очередь):
```bash
python -m scripts.manage init-db
python -m scripts.manage create-partner --name "Site" --credits 100000   # ключ для сайта
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Перед этим — NGINX/Caddy + Let's Encrypt на `api.твойдомен.com` → проксирует на `127.0.0.1:8000`.

> Redis нужен только для rate-limit (не критично, деградирует безопасно). Можно
> поднять рядом в Docker или временно не ставить.

## Порядок вызова (гарантирует корректность)

1. Сайт → `POST /generate` (с `X-API-Key`) → API делает `hold` кредитов, шлёт `/run`, отдаёт `task_id`.
2. Сайт опрашивает `GET /tasks/{id}` → показывает прогресс/результат.
3. RunPod закончил → `POST /runpod/webhook/{secret}` → API: `charge` (успех) или `refund` (ошибка) + сохраняет `result_url` + (опц.) зовёт `callback_url` сайта.
4. Потерялся webhook? Крон гоняет `reconcile_runpod_tasks` (см. `app/workers/maintenance.py`) → добивает по `/status`.

## Загрузка твоего ZIP на RunPod

ZIP с моделями/воркфлоу разложи так:
- **модели** → на RunPod **Network Volume** в `/runpod-volume/models/...` (пути = `config/models.json`);
- **воркфлоу (ComfyUI API-format)** → в репо `config/workflows/<name>.json`, а режимы — `config/modes/<name>.json`. Они попадают в serverless-образ при сборке `deploy/serverless/Dockerfile`.

Пришлёшь инструкцию/структуру ZIP от RunPod — подгоню `models.json`, `extra_model_paths.yaml` и режимы под твои реальные модели.
