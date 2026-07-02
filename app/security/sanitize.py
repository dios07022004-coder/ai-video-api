"""Input sanitization: prompts and filenames.

* ``sanitize_prompt`` strips control characters, collapses whitespace and caps
  length. It is deliberately conservative — the value flows into workflow JSON
  and then to models — so we neutralize anything that could break JSON injection
  or terminal/log formatting while preserving legitimate prompt text.
* ``safe_filename`` produces a filesystem-safe basename (path-traversal proof).
"""

from __future__ import annotations

import re
import unicodedata

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t]{2,}")
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

MAX_PROMPT_LEN = 8000


def sanitize_prompt(text: str | None, *, max_len: int = MAX_PROMPT_LEN) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_RE.sub("", text)
    text = _WS_RE.sub(" ", text)
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def safe_filename(name: str, *, default: str = "file") -> str:
    """Return a traversal-safe basename. Never contains path separators."""
    base = name.replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKD", base)
    base = _FILENAME_RE.sub("_", base).strip("._")
    return base or default
