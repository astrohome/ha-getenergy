"""Guard the manifest rules that hassfest enforces in CI.

Catching these locally is worth a test: the failure otherwise only shows up
after a push, and the message ("keys are not sorted correctly") is easy to
reintroduce whenever a key is added programmatically.
"""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "getenergy"
    / "manifest.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_keys_are_domain_then_name_then_alphabetical():
    keys = list(_manifest())

    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_required_keys_are_present():
    manifest = _manifest()

    for key in ("domain", "name", "codeowners", "documentation", "version"):
        assert manifest.get(key), f"manifest is missing {key}"


def test_recorder_is_declared_since_statistics_are_written():
    assert "recorder" in _manifest()["dependencies"]
