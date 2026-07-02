"""Internal config domain models: Mode, ModelDef and parameter specs.

These are loaded from JSON in `config/` and validated with Pydantic so a
malformed mode fails loudly at load time (not mid-generation). They are the
contract that makes the platform modular: adding capability = adding data here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.constants import ModelType, TaskType


class ParamSpec(BaseModel):
    """Declarative spec for one tunable parameter of a mode.

    Drives: default injection, request-override validation, clamping and the
    `params` block surfaced to the website in GET /modes.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["int", "float", "str", "bool", "enum", "seed"] = "str"
    default: Any = None
    # numeric bounds
    min: float | int | None = None
    max: float | int | None = None
    # enum / choice constraint (e.g. samplers, schedulers)
    choices: list[Any] | None = None
    # placeholder this param feeds, e.g. "STEPS" → {{STEPS}}. Defaults to the
    # uppercased param key.
    placeholder: str | None = None
    # if True the website may override it via request.metadata; else it's fixed.
    overridable: bool = True
    description: str | None = None

    def resolved_placeholder(self, key: str) -> str:
        return (self.placeholder or key).upper()


class ModelDef(BaseModel):
    """One entry in models.json — a checkpoint/LoRA/VAE/controlnet/etc."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: ModelType
    # Filename as ComfyUI expects it (relative to its model dir). No absolute
    # host paths are hardcoded — ComfyUI resolves within its own model roots.
    path: str
    vram_gb: float | None = None
    default_settings: dict[str, Any] = Field(default_factory=dict)
    compatible_workflows: list[str] = Field(default_factory=list)
    enabled: bool = True


class Mode(BaseModel):
    """A user-facing generation mode. One mode → one workflow → one model set."""

    # protected_namespaces=() so fields like `model` / `model_bindings` are allowed.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    id: str
    name: str
    description: str = ""
    category: str = "general"
    task_type: TaskType = TaskType.VIDEO
    enabled: bool = True

    # Wiring
    workflow: str = Field(..., description="Workflow file name (without .json) in config/workflows.")
    model: str = Field(..., description="Primary model id from models.json.")
    # Optional extra model bindings by placeholder, e.g. {"LORA": "anime-lora"}.
    model_bindings: dict[str, str] = Field(default_factory=dict)
    control_video: str | None = None  # filename under config/control/

    # Prompting
    prompt_template: str = "{prompt}"
    negative_prompt: str = ""

    # Tunables
    params: dict[str, ParamSpec] = Field(default_factory=dict)

    # Commerce / presentation
    price_credits: int = 0
    preview: str | None = None

    @model_validator(mode="after")
    def _fill_placeholders(self) -> Mode:
        # Ensure every param has an explicit placeholder for deterministic mapping.
        for key, spec in self.params.items():
            if spec.placeholder is None:
                spec.placeholder = key.upper()
        return self

    def public_params(self) -> dict[str, Any]:
        """The `params` block returned by GET /modes (defaults + constraints)."""
        out: dict[str, Any] = {}
        for key, spec in self.params.items():
            entry: dict[str, Any] = {"type": spec.type, "default": spec.default}
            if spec.min is not None:
                entry["min"] = spec.min
            if spec.max is not None:
                entry["max"] = spec.max
            if spec.choices is not None:
                entry["choices"] = spec.choices
            entry["overridable"] = spec.overridable
            out[key] = entry
        return out
