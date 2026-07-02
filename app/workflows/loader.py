"""Filesystem access for ComfyUI workflow JSON files.

Workflows are stored as ComfyUI **API-format** JSON (the format returned by
"Save (API Format)" in ComfyUI) with ``{{PLACEHOLDER}}`` tokens embedded in node
input values. Node IDs are never referenced by the code — only placeholders.

All names are sanitized to prevent path traversal (Security requirement).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.api.errors import InvalidWorkflowError, WorkflowNotFoundError
from app.logging import get_logger

logger = get_logger("workflows.loader")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class WorkflowLoader:
    """Read/write/list workflow definition files under a base directory."""

    def __init__(self, workflows_dir: Path) -> None:
        self._dir = workflows_dir

    def _safe_path(self, name: str) -> Path:
        stem = name[:-5] if name.endswith(".json") else name
        if not _SAFE_NAME.match(stem):
            raise WorkflowNotFoundError(
                "Invalid workflow name.", details={"name": name}
            )
        path = (self._dir / f"{stem}.json").resolve()
        # Path-traversal guard: resolved path must stay within the workflows dir.
        if self._dir.resolve() not in path.parents and path.parent != self._dir.resolve():
            raise WorkflowNotFoundError("Invalid workflow path.", details={"name": name})
        return path

    def list_names(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))

    def exists(self, name: str) -> bool:
        return self._safe_path(name).exists()

    def load(self, name: str) -> dict:
        path = self._safe_path(name)
        if not path.exists():
            raise WorkflowNotFoundError(details={"name": name})
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidWorkflowError(
                "Workflow file is not valid JSON.", details={"name": name, "error": str(exc)}
            ) from exc

    def load_raw(self, name: str) -> str:
        path = self._safe_path(name)
        if not path.exists():
            raise WorkflowNotFoundError(details={"name": name})
        return path.read_text(encoding="utf-8")

    def save(self, name: str, content: str) -> Path:
        """Validate JSON then atomically write. Used by the admin API."""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvalidWorkflowError(
                "Provided workflow content is not valid JSON.", details={"error": str(exc)}
            ) from exc
        if not isinstance(parsed, dict):
            raise InvalidWorkflowError("Workflow root must be a JSON object (API format).")

        path = self._safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.info("workflow_saved", name=name, bytes=len(content))
        return path
