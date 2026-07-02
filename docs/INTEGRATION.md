# Подключение сайта к генерации на RunPod (Serverless)

Эта схема: **твой сайт и твой API остаются как есть**, а тяжёлую генерацию делает
RunPod Serverless-эндпоинт (масштаб до нуля → платишь только за секунды
генерации). Твой сервер — единственный источник правды по пользователям и
кредитам.

```
Пользователь → [Сайт] → [ТВОЙ API/сервер]
                              │ 1) списал кредит (hold)
                              │ 2) POST /run  → RunPod  (async, с webhook)
                              ▼
                     [RunPod Serverless endpoint]
                              │  ComfyUI + модели (с network volume)
                              ▼
                     готово → webhook на твой сервер
                              │ 3) сохранил video_url, списал кредит (charge)
                              ▼
                     [Сайт] показывает результат пользователю
```

---

## Часть 1. Развёртывание эндпоинта на RunPod (один раз)

### 1.1 Залей модели на Network Volume
1. RunPod → **Storage → Network Volume** → создай том (тот же регион, что и GPU).
2. Смонтируй его к любому дешёвому поду и положи модели в структуру, которую ждёт
   [`deploy/serverless/extra_model_paths.yaml`](../deploy/serverless/extra_model_paths.yaml):
   ```
   /runpod-volume/models/checkpoints/svd_xt_1_1.safetensors
   /runpod-volume/models/checkpoints/wan2.2_i2v.safetensors
   /runpod-volume/models/loras/anime_motion_v1.safetensors
   ...
   ```
   Имена файлов = поле `path` в [`config/models.json`](../config/models.json).

### 1.2 Собери и запушь образ
```bash
# из корня проекта (Git-репозиторий у тебя уже есть)
docker build -f deploy/serverless/Dockerfile -t <твой-registry>/aivideo-serverless:latest .
docker push <твой-registry>/aivideo-serverless:latest
```

### 1.3 Создай Serverless Endpoint
RunPod → **Serverless → New Endpoint**:
- **Container image**: `<твой-registry>/aivideo-serverless:latest`
- **Network Volume**: выбери том из 1.1 (примонтируется в `/runpod-volume`)
- **GPU**: под твою модель (SVD ~16 ГБ, WAN2.2 ~24 ГБ)
- **Active Workers = 0** (самое дешёвое; холодный старт) или `1` (тёплый, но платишь всегда)
- **Max Workers**: сколько параллельных генераций максимум
- **Idle Timeout**: 5–30 c; **FlashBoot**: включи (ускоряет холодный старт)
- **Env** (если отдаёшь результат в своё S3/R2, а не base64):
  ```
  AIV_S3_BUCKET=...        AIV_S3_ENDPOINT=https://<acct>.r2.cloudflarestorage.com
  AIV_S3_ACCESS_KEY=...    AIV_S3_SECRET_KEY=...
  AIV_S3_PUBLIC_BASE=https://cdn.твойдомен.com   # если бакет публичный
  ```
Скопируй **Endpoint ID** и создай **RunPod API Key** (Settings → API Keys).

> Добавляешь новый режим/воркфлоу/модель? Правишь `config/*` и модели на томе,
> пересобираешь образ (или монтируешь `config/` с тома). Код не трогаешь.

---

## Часть 2. Контракт запроса

**Твой сервер → RunPod** (`POST https://api.runpod.ai/v2/<ENDPOINT_ID>/run`):
```jsonc
{
  "input": {
    "mode": "image_to_video",
    "prompt": "a running horse",
    "image": "<base64>",              // ИЛИ "image_url": "https://..."
    "params": { "STEPS": 30, "SEED": 12345 },
    "request_id": "твой-uuid"         // для сопоставления у себя
  },
  "webhook": "https://твой-сервер/runpod/webhook"   // рекомендуется
}
```
Ответ сразу: `{ "id": "job-abc", "status": "IN_QUEUE" }`.

**Готовый результат** (приходит на webhook или через `GET /status/{id}`):
```jsonc
{
  "id": "job-abc",
  "status": "COMPLETED",
  "output": {
    "status": "COMPLETED",
    "mode": "image_to_video",
    "request_id": "твой-uuid",
    "seed": 12345,
    "duration_ms": 41230,
    "delivery": "url",
    "video_url": "https://cdn.../results/2026/07/02/xxxx.mp4"
    // если S3 не настроен: "delivery":"base64","video_base64":"...","content_type":"video/mp4"
  }
}
```
При ошибке: `output.error = { code, message }`.

---

## Часть 3. Код для ТВОЕГО сервера

### 3.1 Python (FastAPI/Flask-агностично)
```python
import os, httpx

RUNPOD_ENDPOINT = os.environ["RUNPOD_ENDPOINT_ID"]
RUNPOD_KEY = os.environ["RUNPOD_API_KEY"]
BASE = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT}"
HEADERS = {"Authorization": f"Bearer {RUNPOD_KEY}"}

async def start_generation(mode, image_b64, prompt, params, request_id, webhook_url):
    payload = {
        "input": {
            "mode": mode, "prompt": prompt, "image": image_b64,
            "params": params, "request_id": request_id,
        },
        "webhook": webhook_url,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/run", json=payload, headers=HEADERS)
        r.raise_for_status()
        return r.json()["id"]            # job_id — сохрани у себя рядом с задачей

async def get_status(job_id):            # запасной путь, если не используешь webhook
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}/status/{job_id}", headers=HEADERS)
        return r.json()
```

### 3.2 Webhook-приёмник (твой сервер) — где ты списываешь кредиты
```python
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/runpod/webhook")
async def runpod_webhook(req: Request):
    body = await req.json()
    job_id = body["id"]
    out = body.get("output") or {}
    if body["status"] == "COMPLETED" and "error" not in out:
        video_url = out.get("video_url")
        request_id = out.get("request_id")
        # 1) найди свою задачу по request_id/job_id
        # 2) сохрани video_url, поставь статус completed
        # 3) спиши кредит окончательно (charge)
    else:
        # верни кредит (refund), пометь задачу failed, залогируй out.get("error")
        ...
    return {"ok": True}
```
> Открытый webhook стоит защитить: добавь в URL секретный токен-путь
> (`/runpod/webhook/<secret>`) или сверяй `request_id` со своей БД.

### 3.3 Node.js (если сайт-бэкенд на JS)
```js
const BASE = `https://api.runpod.ai/v2/${process.env.RUNPOD_ENDPOINT_ID}`;
const H = { Authorization: `Bearer ${process.env.RUNPOD_API_KEY}`, "Content-Type": "application/json" };

export async function startGeneration({ mode, imageB64, prompt, params, requestId, webhookUrl }) {
  const res = await fetch(`${BASE}/run`, {
    method: "POST", headers: H,
    body: JSON.stringify({
      input: { mode, prompt, image: imageB64, params, request_id: requestId },
      webhook: webhookUrl,
    }),
  });
  if (!res.ok) throw new Error(`runpod ${res.status}`);
  return (await res.json()).id;      // job_id
}
```

---

## Часть 4. Правильный порядок у тебя (чтобы «всё корректно»)

1. Пользователь жмёт «Генерировать» → твой API проверяет **баланс кредитов**.
2. Создаёшь задачу в **своей** БД (`status=queued`, `request_id`), делаешь **hold**.
3. Вызываешь `/run` с `webhook` → сохраняешь `job_id`.
4. Отдаёшь сайту `task_id` сразу (не ждёшь). Сайт **опрашивает твой** `GET /tasks/{id}`.
5. Приходит webhook → сохраняешь `video_url`, `status=completed`, **charge**.
6. Ошибка/таймаут → `status=failed`, **refund**.
7. Периодически подчищай «зависшие» (нет webhook за N минут → `GET /status`, добей вручную).

Итог: сайт всегда отвечает мгновенно, кредиты списываются только за успешную
генерацию, а GPU включается лишь на время работы.

---

## Часть 5. Про деньги и задержку

| Настройка | Дешевле | Быстрее |
|---|---|---|
| Active Workers | `0` (до нуля) | `1+` (тёплый) |
| FlashBoot | вкл | вкл |
| Idle Timeout | меньше | больше |
| Результат | S3/R2 URL (мелкий ответ) | — |

- Холодный старт видео-модели: ~30–120 c. Для видео это терпимо (сама генерация
  дольше). Нужна низкая задержка — держи 1 Active Worker.
- Не возвращай большие видео в base64 через `/status` — RunPod ограничивает размер
  ответа. Настрой S3/R2 (env выше) → отдаёшь короткий `video_url`.
- Несколько GPU параллельно = просто подними **Max Workers**. Очередь у RunPod
  встроенная, менять код не нужно.

---

## Альтернатива: постоянный под (если трафик почти непрерывный)
Тогда используй основной [`Dockerfile`](../Dockerfile) + [`docs/DEPLOY_RUNPOD.md`](DEPLOY_RUNPOD.md):
поднимаешь мой FastAPI+RQ+Redis на GPU-поде, твой сервер ходит в него по HTTP.
Дороже на простоях, но нет холодных стартов. Serverless выгоднее при неравномерной
нагрузке — для сайта с пользователями это почти всегда так.
