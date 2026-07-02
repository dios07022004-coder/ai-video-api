"""RunPod Serverless adapter.

Wraps the same modular core used by the FastAPI service (registry + parameter
resolver + workflow engine + ComfyUI client) behind a single serverless
``handler(job)`` function. This is the cost-optimal deployment: the endpoint
scales to zero and bills only for actual generation seconds. Your existing
website/API stays the source of truth for users and credits — it just calls this
endpoint over RunPod's REST (`/run` + webhook/`/status`).
"""

from app.serverless.handler import handler, process

__all__ = ["handler", "process"]
