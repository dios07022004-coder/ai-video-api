"""Workflow Engine — programmatic placeholder injection.

Given a ComfyUI API-format graph and a resolved parameter map, produce a concrete
graph ready to submit. The engine is **generic and node-ID agnostic**: it walks
every value in the graph and replaces ``{{PLACEHOLDER}}`` tokens. It never refers
to node IDs, so workflow authors may freely restructure graphs.

Substitution rules
------------------
* A value that is *exactly* one token (``"{{STEPS}}"``) is replaced by the typed
  parameter value (int stays int, so ComfyUI receives ``20`` not ``"20"``).
* A value that *embeds* tokens (``"a photo of {{PROMPT}}, cinematic"``) is treated
  as a template and the tokens are stringified in place.
* Tokens are matched case-insensitively on ``{{UPPER_SNAKE}}`` and whitespace-
  tolerant (``{{ SEED }}``).

Validation
----------
* ``discover(graph)`` lists every placeholder used.
* ``render`` raises ``PlaceholderUnresolvedError`` if any token remains unfilled,
  reporting exactly which ones — so failures happen before submission, not deep
  inside ComfyUI.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.api.errors import PlaceholderUnresolvedError
from app.config.constants import PLACEHOLDER_PATTERN
from app.logging import get_logger

logger = get_logger("comfy.engine")

_TOKEN_RE = re.compile(PLACEHOLDER_PATTERN)


def _exact_token(value: str) -> str | None:
    """Return the token name if ``value`` is exactly one placeholder, else None."""
    m = re.fullmatch(r"\s*\{\{\s*([A-Z0-9_]+)\s*\}\}\s*", value)
    return m.group(1) if m else None


class WorkflowEngine:
    """Pure, dependency-free placeholder resolver over a ComfyUI graph."""

    def discover(self, graph: dict[str, Any]) -> set[str]:
        """Return the set of placeholder names present anywhere in the graph."""
        found: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, str):
                found.update(m.group(1) for m in _TOKEN_RE.finditer(node))
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(graph)
        return found

    def render(
        self,
        graph: dict[str, Any],
        params: dict[str, Any],
        *,
        allow_missing: set[str] | None = None,
    ) -> dict[str, Any]:
        """Return a new graph with all placeholders substituted from ``params``.

        ``params`` keys are placeholder names (UPPER_SNAKE). ``allow_missing``
        placeholders are permitted to remain unresolved (they will be pruned to
        empty strings) — used for optional bindings such as an absent LoRA.
        """
        allow_missing = allow_missing or set()
        # Normalize param keys to upper for case-insensitive lookup.
        norm = {str(k).upper(): v for k, v in params.items()}

        def substitute(node: Any) -> Any:
            if isinstance(node, str):
                token = _exact_token(node)
                if token is not None:
                    if token in norm:
                        return norm[token]  # preserve type
                    if token in allow_missing:
                        return ""
                    return node  # leave for the unresolved-check to catch
                # embedded template — stringify each token
                def repl(m: re.Match[str]) -> str:
                    name = m.group(1)
                    if name in norm:
                        return str(norm[name])
                    if name in allow_missing:
                        return ""
                    return m.group(0)

                return _TOKEN_RE.sub(repl, node)
            if isinstance(node, dict):
                return {k: substitute(v) for k, v in node.items()}
            if isinstance(node, list):
                return [substitute(v) for v in node]
            return node

        rendered = substitute(copy.deepcopy(graph))

        # Verify nothing is left unresolved.
        remaining = self.discover(rendered) - allow_missing
        if remaining:
            raise PlaceholderUnresolvedError(
                "Workflow still contains unresolved placeholders after injection.",
                details={"unresolved": sorted(remaining), "provided": sorted(norm.keys())},
            )

        logger.debug("workflow_rendered", placeholders=sorted(norm.keys()))
        return rendered

    def validate_coverage(self, graph: dict[str, Any], available: set[str]) -> set[str]:
        """Return placeholders required by the graph but not in ``available``.

        Used at mode-load / admin-save time to catch a workflow that needs a
        token the mode never supplies.
        """
        return {p.upper() for p in self.discover(graph)} - {a.upper() for a in available}
