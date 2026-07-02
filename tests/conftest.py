"""Test configuration: isolate DB/storage into a temp dir before app import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must run before any `app.config.settings.get_settings()` call is cached.
_TMP = Path(tempfile.mkdtemp(prefix="aivideo-test-"))
os.environ.setdefault("AIV_ENV", "dev")
os.environ.setdefault("AIV_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("AIV_LOG_DIR", str(_TMP / "logs"))
os.environ.setdefault("AIV_LOG_JSON", "false")
os.environ.setdefault("AIV_DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ.setdefault("AIV_ADMIN_TOKEN", "test-admin-token")
# Keep the shipped ./config so registry tests find the sample modes.
os.environ.setdefault("AIV_CONFIG_DIR", "./config")
