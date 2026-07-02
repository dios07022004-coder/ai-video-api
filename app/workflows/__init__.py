"""Dynamic configuration registry for modes, workflows and models.

This package is the heart of the platform's modularity: adding a mode / workflow
/ model is a data change under ``config/``, picked up by a hot reload — never a
code change.
"""

from app.workflows.loader import WorkflowLoader
from app.workflows.registry import Registry, get_registry

__all__ = ["Registry", "get_registry", "WorkflowLoader"]
