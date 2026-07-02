"""In-memory, hot-reloadable registry of modes and models.

Loaded from ``config/modes/*.json`` and ``config/models.json`` at startup and on
``POST /admin/modes/reload``. Reads are lock-free against a snapshot; a reload
builds a fresh snapshot and swaps it atomically so in-flight requests never see a
half-loaded state.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.api.errors import (
    ModeNotFoundError,
    ModelNotFoundError,
)
from app.config.constants import TaskType
from app.config.settings import Settings, get_settings
from app.logging import get_logger
from app.models.definitions import Mode, ModelDef
from app.workflows.loader import WorkflowLoader

logger = get_logger("workflows.registry")


@dataclass(frozen=True)
class RegistrySnapshot:
    modes: dict[str, Mode] = field(default_factory=dict)
    models: dict[str, ModelDef] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)


class Registry:
    """Thread-safe holder of the current configuration snapshot."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._loader = WorkflowLoader(self._settings.workflows_dir)
        self._lock = threading.RLock()
        self._snapshot = RegistrySnapshot()

    # ── loading ──────────────────────────────────────────────────────────────
    def reload(self) -> dict[str, Any]:
        """Rebuild the snapshot from disk. Returns a summary for the admin API."""
        modes: dict[str, Mode] = {}
        models: dict[str, ModelDef] = {}
        errors: list[dict[str, Any]] = []

        # models.json
        models_path = self._settings.models_path
        if models_path.exists():
            try:
                raw = json.loads(models_path.read_text(encoding="utf-8"))
                items = raw.get("models", raw) if isinstance(raw, dict) else raw
                for entry in items:
                    try:
                        m = ModelDef.model_validate(entry)
                        models[m.id] = m
                    except ValidationError as exc:
                        errors.append({"file": "models.json", "id": entry.get("id"), "error": exc.errors()})
            except (json.JSONDecodeError, OSError) as exc:
                errors.append({"file": "models.json", "error": str(exc)})

        # modes/*.json
        modes_dir = self._settings.modes_dir
        if modes_dir.exists():
            for path in sorted(modes_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data.setdefault("id", path.stem)
                    mode = Mode.model_validate(data)
                    self._validate_wiring(mode, models, errors, path)
                    modes[mode.id] = mode
                except (json.JSONDecodeError, OSError) as exc:
                    errors.append({"file": path.name, "error": str(exc)})
                except ValidationError as exc:
                    errors.append({"file": path.name, "error": exc.errors()})

        snapshot = RegistrySnapshot(modes=modes, models=models, errors=errors)
        with self._lock:
            self._snapshot = snapshot

        logger.info(
            "registry_reloaded",
            modes=len(modes),
            models=len(models),
            errors=len(errors),
        )
        return {
            "modes": len(modes),
            "models": len(models),
            "workflows": len(self._loader.list_names()),
            "errors": errors,
        }

    def _validate_wiring(
        self,
        mode: Mode,
        models: dict[str, ModelDef],
        errors: list[dict[str, Any]],
        path: Path,
    ) -> None:
        if not self._loader.exists(mode.workflow):
            errors.append({"file": path.name, "warning": f"workflow '{mode.workflow}' not found"})
        if mode.model and mode.model not in models:
            errors.append({"file": path.name, "warning": f"model '{mode.model}' not in models.json"})
        for placeholder, model_id in mode.model_bindings.items():
            if model_id not in models:
                errors.append(
                    {"file": path.name, "warning": f"binding {placeholder}->'{model_id}' unknown model"}
                )

    # ── reads (snapshot) ─────────────────────────────────────────────────────
    @property
    def snapshot(self) -> RegistrySnapshot:
        with self._lock:
            return self._snapshot

    @property
    def loader(self) -> WorkflowLoader:
        return self._loader

    def list_modes(self, task_type: TaskType | None = None) -> list[Mode]:
        modes = list(self.snapshot.modes.values())
        if task_type is not None:
            modes = [m for m in modes if m.task_type == task_type]
        return sorted(modes, key=lambda m: (m.category, m.name))

    def get_mode(self, mode_id: str) -> Mode:
        mode = self.snapshot.modes.get(mode_id)
        if mode is None:
            raise ModeNotFoundError(details={"mode": mode_id})
        return mode

    def get_model(self, model_id: str) -> ModelDef:
        model = self.snapshot.models.get(model_id)
        if model is None:
            raise ModelNotFoundError(details={"model": model_id})
        return model

    def try_get_model(self, model_id: str) -> ModelDef | None:
        return self.snapshot.models.get(model_id)

    def upsert_mode_file(self, mode_id: str, data: dict[str, Any]) -> Mode:
        """Persist a mode JSON (admin edit) then reload just that mode into the snapshot."""
        data.setdefault("id", mode_id)
        mode = Mode.model_validate(data)  # validates before writing
        path = self._settings.modes_dir / f"{mode_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(mode.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        with self._lock:
            new_modes = dict(self._snapshot.modes)
            new_modes[mode_id] = mode
            self._snapshot = RegistrySnapshot(
                modes=new_modes, models=self._snapshot.models, errors=self._snapshot.errors
            )
        logger.info("mode_upserted", mode=mode_id)
        return mode

    def raw_mode(self, mode_id: str) -> dict[str, Any]:
        path = self._settings.modes_dir / f"{mode_id}.json"
        if not path.exists():
            raise ModeNotFoundError(details={"mode": mode_id})
        return json.loads(path.read_text(encoding="utf-8"))


_registry: Registry | None = None
_registry_lock = threading.Lock()


def get_registry() -> Registry:
    """Process-wide registry singleton (lazy, loaded on first access)."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                reg = Registry()
                reg.reload()
                _registry = reg
    return _registry
