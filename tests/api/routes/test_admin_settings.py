"""Compatibility wrapper for backend admin settings route tests.

Canonical tests live under `backend/tests/...`; this path exists so root-level
GSD/pytest gates do not fail before reaching real verification.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HAS_BACKEND_DEPS = importlib.util.find_spec("httpx_ws") is not None

if _HAS_BACKEND_DEPS:
    _CANONICAL = Path(__file__).resolve().parents[3] / "backend/tests/api/routes/test_admin_settings.py"
    _SPEC = importlib.util.spec_from_file_location("_backend_test_admin_settings", _CANONICAL)
    assert _SPEC and _SPEC.loader
    _MODULE = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_MODULE)
    globals().update(
        {
            name: value
            for name, value in vars(_MODULE).items()
            if name.startswith("test_") or name.startswith("Test")
        }
    )
else:

    def test_backend_admin_settings_tests_require_backend_uv_environment() -> None:
        pytest.skip("Run canonical backend tests with `uv run --project backend pytest ...`")
