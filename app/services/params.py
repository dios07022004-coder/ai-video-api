"""Parameter resolution — turns a mode + request into a placeholder map.

Precedence (lowest → highest):
    1. mode param `default`
    2. model `default_settings`
    3. request `metadata` override (only if the param is `overridable`)
    4. safety clamps (min/max, enum membership)

The output is the exact key/value map fed to the Workflow Engine, plus the
resolved prompt/negative persisted for auditing. This is where "inject
parameters" happens — declaratively, driven entirely by the mode JSON.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from app.api.errors import ParamInvalidError
from app.models.definitions import Mode, ModelDef
from app.security.sanitize import sanitize_prompt

_MAX_SEED = 2**32 - 1


@dataclass
class ResolvedParams:
    placeholders: dict[str, Any] = field(default_factory=dict)  # UPPER -> value
    prompt: str = ""
    negative: str = ""
    optional_missing: set[str] = field(default_factory=set)     # allowed-unresolved tokens

    def as_public(self) -> dict[str, Any]:
        """Subset safe to echo back to the caller (no internal-only tokens)."""
        return dict(self.placeholders)


class ParamResolver:
    """Pure resolver (no I/O). Reused by generation and preview."""

    def resolve(
        self,
        mode: Mode,
        *,
        prompt: str | None,
        overrides: dict[str, Any] | None,
        image_comfy_name: str | None = None,
        model: ModelDef | None = None,
        extra_models: dict[str, ModelDef] | None = None,
        control_video: str | None = None,
    ) -> ResolvedParams:
        overrides = overrides or {}
        extra_models = extra_models or {}
        placeholders: dict[str, Any] = {}

        model_defaults = model.default_settings if model else {}

        # 1-4: per-param resolution
        for key, spec in mode.params.items():
            token = (spec.placeholder or key).upper()
            value: Any = spec.default
            if key in model_defaults:
                value = model_defaults[key]
            if spec.overridable and key in overrides:
                value = overrides[key]
            value = self._coerce_and_validate(key, spec, value)
            placeholders[token] = value

        # Prompt / negative
        clean_prompt = sanitize_prompt(prompt)
        final_prompt = mode.prompt_template.replace("{prompt}", clean_prompt).strip(", ").strip()
        negative = sanitize_prompt(mode.negative_prompt)
        placeholders["PROMPT"] = final_prompt
        placeholders["NEGATIVE"] = negative

        # Wiring placeholders (image, model, extra model bindings, control video)
        optional_missing: set[str] = set()
        if image_comfy_name is not None:
            placeholders["IMAGE"] = image_comfy_name
        if model is not None:
            placeholders.setdefault("MODEL", model.path)
            placeholders["MODEL"] = model.path
        for token, mdef in extra_models.items():
            placeholders[token.upper()] = mdef.path
        if control_video is not None:
            placeholders["CONTROL_VIDEO"] = control_video

        # Tokens the workflow may reference but that are legitimately absent
        # (e.g. an optional LoRA) are allowed to resolve to empty.
        for token in ("LORA", "VAE", "CONTROLNET", "IPADAPTER", "CONTROL_VIDEO"):
            if token not in placeholders:
                optional_missing.add(token)

        return ResolvedParams(
            placeholders=placeholders,
            prompt=final_prompt,
            negative=negative,
            optional_missing=optional_missing,
        )

    # ── validation / coercion ────────────────────────────────────────────────
    def _coerce_and_validate(self, key: str, spec, value: Any) -> Any:  # noqa: ANN001
        t = spec.type
        try:
            if t == "seed":
                iv = int(value) if value not in (None, "", -1, "-1") else -1
                if iv < 0:
                    iv = secrets.randbelow(_MAX_SEED)
                return iv
            if t == "int":
                value = int(value)
            elif t == "float":
                value = float(value)
            elif t == "bool":
                value = bool(value) if not isinstance(value, str) else value.lower() in {"1", "true", "yes"}
            elif t == "enum":
                if spec.choices is not None and value not in spec.choices:
                    raise ParamInvalidError(
                        f"Parameter '{key}' must be one of {spec.choices}.",
                        details={"param": key, "value": value, "choices": spec.choices},
                    )
            elif t == "str":
                value = str(value) if value is not None else ""
        except (TypeError, ValueError) as exc:
            raise ParamInvalidError(
                f"Parameter '{key}' has an invalid value for type {t}.",
                details={"param": key, "value": value, "expected": t},
            ) from exc

        # numeric clamping
        if t in {"int", "float"}:
            if spec.min is not None and value < spec.min:
                value = spec.min
            if spec.max is not None and value > spec.max:
                value = spec.max
        return value
