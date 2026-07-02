"""Registry loads the shipped config and wires modes → workflows → models."""

from __future__ import annotations

from app.comfy.engine import WorkflowEngine
from app.workflows.registry import Registry


def test_sample_modes_load() -> None:
    reg = Registry()
    summary = reg.reload()
    assert summary["modes"] >= 1
    ids = {m.id for m in reg.list_modes()}
    assert "french_kiss" in ids


def test_mode_workflow_placeholders_are_covered() -> None:
    """Every placeholder a shipped workflow needs must be resolvable from the
    mode's params + model bindings + standard wiring tokens — otherwise
    generation would fail on an unresolved placeholder."""
    reg = Registry()
    reg.reload()
    engine = WorkflowEngine()
    standard = {"IMAGE", "PROMPT", "NEGATIVE", "MODEL", "CONTROL_VIDEO", "LORA", "VAE", "SEED"}
    for mode in reg.list_modes():
        graph = reg.loader.load(mode.workflow)
        available = (
            {p.upper() for p in mode.params}
            | {b.upper() for b in mode.model_bindings}
            | standard
        )
        missing = engine.validate_coverage(graph, available)
        assert not missing, f"mode {mode.id} misses placeholders {missing}"


def test_get_model_and_mode() -> None:
    reg = Registry()
    reg.reload()
    mode = reg.get_mode("french_kiss")
    assert mode.price_credits == 20
    assert mode.workflow == "french_kiss"
    model = reg.get_model(mode.model)
    assert model.type == "checkpoint"
    # model bindings resolve to real model definitions
    for model_id in mode.model_bindings.values():
        assert reg.get_model(model_id) is not None
