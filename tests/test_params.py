"""ParamResolver tests: defaults, precedence, clamping, validation."""

from __future__ import annotations

import pytest

from app.api.errors import ParamInvalidError
from app.models.definitions import Mode, ParamSpec
from app.services.params import ParamResolver


def _mode(**params: ParamSpec) -> Mode:
    return Mode(
        id="m",
        name="M",
        workflow="wf",
        model="mdl",
        prompt_template="hero, {prompt}",
        negative_prompt="bad",
        params=params,
    )


@pytest.fixture
def resolver() -> ParamResolver:
    return ParamResolver()


def test_defaults_applied(resolver: ParamResolver) -> None:
    mode = _mode(STEPS=ParamSpec(type="int", default=20))
    r = resolver.resolve(mode, prompt="a dog", overrides=None)
    assert r.placeholders["STEPS"] == 20
    assert r.prompt == "hero, a dog"
    assert r.negative == "bad"


def test_override_precedence_and_clamp(resolver: ParamResolver) -> None:
    mode = _mode(STEPS=ParamSpec(type="int", default=20, min=1, max=50))
    r = resolver.resolve(mode, prompt=None, overrides={"STEPS": 999})
    assert r.placeholders["STEPS"] == 50  # clamped to max


def test_non_overridable_ignores_request(resolver: ParamResolver) -> None:
    mode = _mode(CFG=ParamSpec(type="float", default=2.5, overridable=False))
    r = resolver.resolve(mode, prompt=None, overrides={"CFG": 12.0})
    assert r.placeholders["CFG"] == 2.5


def test_enum_validation(resolver: ParamResolver) -> None:
    mode = _mode(SAMPLER=ParamSpec(type="enum", default="euler", choices=["euler", "ddim"]))
    with pytest.raises(ParamInvalidError):
        resolver.resolve(mode, prompt=None, overrides={"SAMPLER": "nope"})


def test_seed_randomized_when_negative(resolver: ParamResolver) -> None:
    mode = _mode(SEED=ParamSpec(type="seed", default=-1))
    r = resolver.resolve(mode, prompt=None, overrides=None)
    assert isinstance(r.placeholders["SEED"], int)
    assert r.placeholders["SEED"] >= 0


def test_optional_tokens_marked_missing(resolver: ParamResolver) -> None:
    mode = _mode(STEPS=ParamSpec(type="int", default=1))
    r = resolver.resolve(mode, prompt=None, overrides=None)
    assert "LORA" in r.optional_missing
