"""Mode listing + preview (read-only against the registry)."""

from __future__ import annotations

from app.api.errors import ModeDisabledError
from app.config.constants import TaskType
from app.models.definitions import Mode
from app.schemas.modes import ModeInfo, PreviewResponse
from app.services.params import ParamResolver
from app.workflows.registry import Registry


class ModeService:
    def __init__(self, registry: Registry, resolver: ParamResolver | None = None) -> None:
        self._registry = registry
        self._resolver = resolver or ParamResolver()

    def list_modes(self, task_type: TaskType | None = None, *, include_disabled: bool = False) -> list[ModeInfo]:
        modes = self._registry.list_modes(task_type)
        return [self._to_info(m) for m in modes if include_disabled or m.enabled]

    def get_mode(self, mode_id: str) -> Mode:
        return self._registry.get_mode(mode_id)

    def preview(self, mode_id: str, prompt: str | None) -> PreviewResponse:
        mode = self._registry.get_mode(mode_id)
        if not mode.enabled:
            raise ModeDisabledError(details={"mode": mode_id})
        model = self._registry.try_get_model(mode.model)
        resolved = self._resolver.resolve(mode, prompt=prompt, overrides=None, model=model)
        return PreviewResponse(
            mode=mode.id,
            prompt=resolved.prompt,
            negative=resolved.negative,
            params=resolved.as_public(),
        )

    def _to_info(self, mode: Mode) -> ModeInfo:
        model = self._registry.try_get_model(mode.model)
        return ModeInfo(
            id=mode.id,
            name=mode.name,
            category=mode.category,
            enabled=mode.enabled,
            model=model.name if model else mode.model,
            control_video=mode.control_video,
            params=mode.public_params(),
        )
