"""WorkflowEngine placeholder-injection tests."""

from __future__ import annotations

import pytest

from app.api.errors import PlaceholderUnresolvedError
from app.comfy.engine import WorkflowEngine


@pytest.fixture
def engine() -> WorkflowEngine:
    return WorkflowEngine()


def test_exact_token_preserves_type(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"steps": "{{STEPS}}", "cfg": "{{CFG}}"}}}
    out = engine.render(graph, {"STEPS": 20, "CFG": 2.5})
    assert out["1"]["inputs"]["steps"] == 20  # int, not "20"
    assert out["1"]["inputs"]["cfg"] == 2.5


def test_embedded_template_stringifies(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"text": "a photo of {{PROMPT}}, {{STYLE}}"}}}
    out = engine.render(graph, {"PROMPT": "a cat", "STYLE": "cinematic"})
    assert out["1"]["inputs"]["text"] == "a photo of a cat, cinematic"


def test_whitespace_and_case_insensitive(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"seed": "{{ SEED }}"}}}
    out = engine.render(graph, {"seed": 7})
    assert out["1"]["inputs"]["seed"] == 7


def test_unresolved_raises(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"x": "{{MISSING}}"}}}
    with pytest.raises(PlaceholderUnresolvedError) as exc:
        engine.render(graph, {})
    assert "MISSING" in exc.value.details["unresolved"]


def test_allow_missing_pruned(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"lora": "{{LORA}}"}}}
    out = engine.render(graph, {}, allow_missing={"LORA"})
    assert out["1"]["inputs"]["lora"] == ""


def test_discover_finds_all(engine: WorkflowEngine) -> None:
    graph = {"a": "{{X}}", "b": ["{{Y}}", {"c": "text {{Z}}"}]}
    assert engine.discover(graph) == {"X", "Y", "Z"}


def test_does_not_mutate_input(engine: WorkflowEngine) -> None:
    graph = {"1": {"inputs": {"steps": "{{STEPS}}"}}}
    engine.render(graph, {"STEPS": 5})
    assert graph["1"]["inputs"]["steps"] == "{{STEPS}}"  # original untouched
