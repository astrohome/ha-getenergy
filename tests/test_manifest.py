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


# -- service registration ----------------------------------------------------

INIT = MANIFEST.parent / "__init__.py"
SERVICES = MANIFEST.parent / "services.yaml"


def test_backfill_is_registered_at_component_setup():
    """Not in async_setup_entry, which awaits the coordinator's first refresh.

    A transient portal failure there aborts setup, and the action then simply
    does not exist -- which is what "Action getenergy.backfill not found"
    actually meant.
    """
    source = INIT.read_text(encoding="utf-8")
    setup = source.split("async def async_setup(")[1].split("async def ")[0]

    assert "async_register(hass)" in setup
    assert "async_register(hass)" not in source.split("async def async_setup_entry(")[1]


def test_declaring_async_setup_requires_a_config_schema():
    assert "CONFIG_SCHEMA" in INIT.read_text(encoding="utf-8")


def test_services_yaml_declares_backfill():
    assert "backfill:" in SERVICES.read_text(encoding="utf-8")


def test_recorder_helper_is_imported_lazily():
    """An internal recorder API must not break the whole integration on import."""
    source = (MANIFEST.parent / "backfill.py").read_text(encoding="utf-8")
    header = source.split("def collect(")[0]

    assert "async_add_external_statistics" not in header
    assert "async_add_external_statistics" in source
