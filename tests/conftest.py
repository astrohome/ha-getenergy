"""Make the integration's leaf modules importable without Home Assistant.

``custom_components/getenergy/__init__.py`` imports Home Assistant, which is
not a test dependency. Registering a stub package with the right ``__path__``
lets ``getenergy.api`` and ``getenergy.bill`` resolve their relative imports
while that ``__init__`` never runs.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent / "custom_components" / "getenergy"

# The PDF fixture helper lives beside the tests; the analysis tool it also
# exercises lives in tools/.
for extra in (HERE, HERE.parent / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

if "getenergy" not in sys.modules:
    stub = types.ModuleType("getenergy")
    stub.__path__ = [str(PACKAGE)]
    sys.modules["getenergy"] = stub
