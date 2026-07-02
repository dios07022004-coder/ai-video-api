"""Structured logging facade.

`get_logger(name)` returns a bound structlog logger. `configure_logging()` wires
JSON (prod) or console (dev) rendering + a rotating file sink. One import path
for the whole codebase so log style is uniform across API, workers and ComfyUI
integration.
"""

from app.logging.setup import bind_context, configure_logging, get_logger

__all__ = ["configure_logging", "get_logger", "bind_context"]
