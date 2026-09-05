#!/usr/bin/env python3
"""Check which bill labels this integration can actually find.

The parser is anchored on the labels printed on your statement. Those labels
were inferred from the portal's data model, not from a confirmed sample of
every layout, so run this once against a real bill to see what matches.

    # against a statement you already have
    python tools/calibrate.py path/to/statement.pdf

    # or download the newest one first
    GETENERGY_USERNAME=you@example.com GETENERGY_PASSWORD=... \
        python tools/calibrate.py --download

Add --text to dump the extracted text, which is what you need if a field comes
back missing and you want to widen its pattern in custom_components/getenergy/
bill.py.

Credentials are read from the environment and are never written anywhere.
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path

# Register a stub package so the leaf modules resolve their relative imports
# without running __init__.py, which imports Home Assistant.
_PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "getenergy"
if "getenergy" not in sys.modules:
    _stub = types.ModuleType("getenergy")
    _stub.__path__ = [str(_PACKAGE)]
    sys.modules["getenergy"] = _stub

from getenergy.bill import FIELDS, extract_text, parse  # noqa: E402


def _download() -> tuple[str, bytes]:
    from getenergy.api import GetEnergyClient
    from getenergy.const import BACKFILL_MONTHS

    user = os.environ.get("GETENERGY_USERNAME")
    pwd = os.environ.get("GETENERGY_PASSWORD")
    if not user or not pwd:
        raise SystemExit("set GETENERGY_USERNAME and GETENERGY_PASSWORD")

    client = GetEnergyClient(username=user, password=pwd)
    client.login()
    return client.latest_consolidated(BACKFILL_MONTHS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", nargs="?", type=Path, help="a statement PDF")
    ap.add_argument("--download", action="store_true", help="fetch the newest bill")
    ap.add_argument("--text", action="store_true", help="dump the extracted text")
    args = ap.parse_args()

    if args.download:
        period, data = _download()
        out = Path(f"consolidated-{period}.pdf")
        out.write_bytes(data)
        print(f"saved {out} ({len(data)} bytes)\n")
    elif args.pdf:
        period, data = args.pdf.stem[-6:], args.pdf.read_bytes()
    else:
        ap.error("give a PDF path or --download")

    if args.text:
        print(extract_text(data))
        print("-" * 60)

    statement = parse(data, period)
    width = max(len(f.key) for f in FIELDS)

    print(f"statement period: {statement.period}\n")
    for spec in FIELDS:
        value = statement.values.get(spec.key)
        mark = "OK  " if value is not None else "MISS"
        shown = f"{value} {spec.unit}" if value is not None else "-"
        print(f"  [{mark}] {spec.key.ljust(width)}  {shown}")

    if statement.missing:
        print(
            f"\n{len(statement.missing)} field(s) had no matching label. Re-run "
            "with --text and widen the patterns in bill.py, then please open a "
            "PR so others benefit."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
