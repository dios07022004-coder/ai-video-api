"""RunPod Serverless process entrypoint.

Started by the container after ComfyUI is up. Registers the async handler with
the RunPod SDK, which owns the job queue, autoscaling and /run + /status REST.
"""

from __future__ import annotations

from app.logging import get_logger
from app.serverless.handler import handler

logger = get_logger("serverless.main")


def main() -> None:
    import runpod  # imported here so the module is testable without the SDK

    logger.info("serverless_starting")
    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
