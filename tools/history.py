#!/usr/bin/env python3
"""Download every available statement and work out your real cost structure.

Home Assistant's energy dashboard computes ``cost = sum(delta_kWh * price)``.
That is a pure multiplication, so it has no way to express a standing charge.
But an Alberta electricity bill is not linear in kWh: the admin charge and part
of the distribution charge are fixed per month, which is why your average cost
per kWh falls the more you import.

The bill prints Transmission and Distribution as single totals and never
publishes the fixed/variable split. This tool recovers it empirically by
regressing each charge component against kWh and days across your statement
history:

    charge ~= fixed + per_kwh * kWh + per_day * days

The per_kwh slopes are what a marginal price entity should use; the fixed and
per_day intercepts are what has to be tracked separately.

    GETENERGY_USERNAME=... GETENERGY_PASSWORD=... python tools/history.py

Writes history.json (one record per statement) and prints the fit. Downloads
are rate-limited and skipped if already on disk, so re-running is cheap.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from datetime import date
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "getenergy"
if "getenergy" not in sys.modules:
    _stub = types.ModuleType("getenergy")
    _stub.__path__ = [str(_PACKAGE)]
    sys.modules["getenergy"] = _stub

from getenergy.api import DocumentUnavailable, GetEnergyClient  # noqa: E402
from getenergy.bill import parse  # noqa: E402

COMPONENTS = [
    "energy_charge",
    "green_offset_charge",
    "microgen_credit",
    "transmission_charge",
    "transmission_riders",
    "distribution_charge",
    "distribution_riders",
    "balancing_pool",
    "franchise_fee",
    "admin_charge",
    "transmission_and_distribution",
]


def solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Least squares via normal equations and Gaussian elimination.

    Deliberately dependency-free -- this runs from a checkout, not inside Home
    Assistant, and pulling in numpy for a 3x3 solve is not worth it.
    """
    n = len(matrix[0])
    ata = [[sum(r[i] * r[j] for r in matrix) for j in range(n)] for i in range(n)]
    atb = [sum(r[i] * y for r, y in zip(matrix, rhs)) for i in range(n)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(ata[r][col]))
        if abs(ata[pivot][col]) < 1e-12:
            return None  # singular: not enough variation to separate the terms
        ata[col], ata[pivot] = ata[pivot], ata[col]
        atb[col], atb[pivot] = atb[pivot], atb[col]
        for row in range(col + 1, n):
            factor = ata[row][col] / ata[col][col]
            for k in range(col, n):
                ata[row][k] -= factor * ata[col][k]
            atb[row] -= factor * atb[col]

    out = [0.0] * n
    for row in reversed(range(n)):
        total = atb[row] - sum(ata[row][k] * out[k] for k in range(row + 1, n))
        out[row] = total / ata[row][row]
    return out


def r_squared(actual: list[float], predicted: list[float]) -> float:
    mean = sum(actual) / len(actual)
    ss_tot = sum((a - mean) ** 2 for a in actual)
    ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    return 1.0 - ss_res / ss_tot if ss_tot else float("nan")


def fit(records: list[dict], key: str) -> dict | None:
    """Fit ``charge ~ fixed + per_kwh*kWh + per_day*days`` for one component."""
    rows, ys = [], []
    for rec in records:
        kwh = rec.get("electricity_imported_kwh")
        days = rec.get("service_days")
        value = rec.get(key)
        if kwh is None or days is None or value is None:
            continue
        rows.append([1.0, float(kwh), float(days)])
        ys.append(float(value))

    if len(rows) < 4:
        return None
    coeffs = solve(rows, ys)
    if coeffs is None:
        return None

    predicted = [sum(c * x for c, x in zip(coeffs, row)) for row in rows]
    return {
        "n": len(rows),
        "fixed": coeffs[0],
        "per_kwh": coeffs[1],
        "per_day": coeffs[2],
        "r2": r_squared(ys, predicted),
    }


def download(months: int, outdir: Path, delay: float) -> list[dict]:
    client = GetEnergyClient(
        username=os.environ["GETENERGY_USERNAME"],
        password=os.environ["GETENERGY_PASSWORD"],
    )
    client.login()
    print(f"signed in as {client.username}\n")

    outdir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    today = date.today()
    year, month = today.year, today.month

    for _ in range(months):
        period = f"{year:04d}{month:02d}"
        pdf_path = outdir / f"consolidated-{period}.pdf"

        if pdf_path.exists():
            pdf = pdf_path.read_bytes()
            note = "cached"
        else:
            try:
                pdf = client.consolidated(period)
            except DocumentUnavailable:
                print(f"  {period}  -")
                month -= 1
                if month == 0:
                    year, month = year - 1, 12
                continue
            pdf_path.write_bytes(pdf)
            note = f"{len(pdf)}B"
            # Be a considerate guest: this is someone else's billing system.
            time.sleep(delay)

        statement = parse(pdf, period)
        record = statement.as_dict()
        records.append(record)
        print(
            f"  {period}  {note:>8}  "
            f"{record.get('electricity_imported_kwh', '?')} kWh in, "
            f"{record.get('electricity_exported_kwh', '?')} kWh out, "
            f"${record.get('amount_due', '?')}"
        )

        month -= 1
        if month == 0:
            year, month = year - 1, 12

    return records


def report(records: list[dict]) -> None:
    print(f"\n{len(records)} statements parsed\n")
    print("=" * 74)
    print("Cost structure   charge ~ fixed + per_kwh*kWh + per_day*days")
    print("=" * 74)
    print(f"{'component':<32}{'fixed':>9}{'$/kWh':>10}{'$/day':>9}{'r2':>8}{'n':>5}")
    print("-" * 74)

    marginal = 0.0
    for key in COMPONENTS:
        result = fit(records, key)
        if result is None:
            print(f"{key:<32}{'insufficient data':>41}")
            continue
        print(
            f"{key:<32}{result['fixed']:>9.2f}{result['per_kwh']:>10.5f}"
            f"{result['per_day']:>9.3f}{result['r2']:>8.3f}{result['n']:>5}"
        )
        # T&D is the sum of its parts; counting both would double it.
        if key != "transmission_and_distribution":
            marginal += result["per_kwh"]

    print("-" * 74)
    print(f"\nmarginal import cost  ~ ${marginal:.5f} / kWh  (excl. GST)")
    print("A low r2 means that component is not well explained by kWh and days;")
    print("treat its split as unreliable rather than rounding it into the rate.")

    rates = {r.get("rate_cents_per_kwh") for r in records if r.get("rate_cents_per_kwh")}
    if rates:
        print(f"\nenergy rates seen: {sorted(rates)} cents/kWh")
        if len(rates) > 1:
            print("Rates changed over this window, so a single fit blends regimes.")
            print("Re-run with --months 12 to restrict it to the recent one.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--months", type=int, default=30, help="how far back to look")
    ap.add_argument("--outdir", type=Path, default=Path("statements"))
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between fetches")
    args = ap.parse_args()

    if not os.environ.get("GETENERGY_USERNAME") or not os.environ.get(
        "GETENERGY_PASSWORD"
    ):
        raise SystemExit("set GETENERGY_USERNAME and GETENERGY_PASSWORD")

    records = download(args.months, args.outdir, args.delay)
    if not records:
        raise SystemExit("no statements found")

    out = Path("history.json")
    out.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")

    report(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
