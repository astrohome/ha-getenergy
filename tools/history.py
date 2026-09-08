#!/usr/bin/env python3
"""Download every available statement and work out your real cost structure.

Home Assistant's energy dashboard computes ``cost = sum(delta_kWh * price)``.
That is a pure multiplication, so it has no way to express a standing charge.
But an Alberta electricity bill is not linear in kWh: the admin charge and part
of the distribution charge are fixed per month, which is why average cost per
kWh falls the more you import.

The bill prints Transmission and Distribution as single totals and never
publishes the fixed/variable split. This tool recovers it by regressing each
component across your statement history:

    charge ~= fixed + per_kwh * kWh + per_day * days

Not every component is regressed against the same thing, and two are not
regressed at all -- see COMPONENTS below for why.

    GETENERGY_USERNAME=... GETENERGY_PASSWORD=... python tools/history.py

Writes history.json and prints the fit. Downloads are cached and rate-limited,
so re-running is cheap.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "getenergy"
if "getenergy" not in sys.modules:
    _stub = types.ModuleType("getenergy")
    _stub.__path__ = [str(_PACKAGE)]
    sys.modules["getenergy"] = _stub

from getenergy.api import DocumentUnavailable, GetEnergyClient  # noqa: E402
from getenergy.bill import parse  # noqa: E402

IMPORTED = "electricity_imported_kwh"
EXPORTED = "electricity_exported_kwh"


@dataclass(frozen=True)
class Component:
    key: str
    #: Which volume drives this charge. Regressing the microgen credit against
    #: imported kWh is simply the wrong predictor -- it is paid on exports.
    predictor: str = IMPORTED
    #: Charges that are rate x volume are not regressed. The energy rate has
    #: moved several-fold across the history, so a fit against kWh alone is
    #: misspecified and returns a meaningless (even negative) slope.
    rate_driven: bool = False
    #: Whether the per-kWh slope belongs in the marginal *import* price.
    marginal_import: bool = True


COMPONENTS: tuple[Component, ...] = (
    Component("energy_charge", rate_driven=True),
    Component("green_offset_charge"),
    Component("microgen_credit", predictor=EXPORTED, rate_driven=True,
              marginal_import=False),
    Component("transmission_charge"),
    Component("transmission_riders"),
    Component("distribution_charge"),
    Component("distribution_riders"),
    Component("balancing_pool"),
    Component("franchise_fee"),
    Component("admin_charge"),
    # Reported for cross-checking only; it is the sum of its parts, so adding
    # its slope to theirs would double-count.
    Component("transmission_and_distribution", marginal_import=False),
)


def solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Least squares via normal equations and Gaussian elimination.

    Dependency-free on purpose: this runs from a checkout, not inside Home
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


def fit(records: list[dict], key: str, predictor: str = IMPORTED) -> dict | None:
    """Fit ``charge ~ fixed + per_unit*volume + per_day*days``."""
    rows, ys = [], []
    for rec in records:
        volume, days, value = rec.get(predictor), rec.get("service_days"), rec.get(key)
        if volume is None or days is None or value is None:
            continue
        rows.append([1.0, float(volume), float(days)])
        ys.append(float(value))

    if len(rows) < 4:
        return None
    coeffs = solve(rows, ys)
    if coeffs is None:
        return None

    predicted = [sum(c * x for c, x in zip(coeffs, row)) for row in rows]
    spread = max(ys) - min(ys)
    return {
        "n": len(rows),
        "fixed": coeffs[0],
        "per_kwh": coeffs[1],
        "per_day": coeffs[2],
        "r2": r_squared(ys, predicted),
        # A charge that barely moves has almost no variance to explain, so its
        # r2 is low however good the fit. Flagging it stops a fixed fee being
        # dismissed as unreliable when "does not depend on kWh" is the answer.
        "nearly_constant": spread < 0.05 * (abs(sum(ys) / len(ys)) or 1.0),
    }


def summarise(fits: dict[str, dict]) -> dict[str, float]:
    """Roll the per-component fits up into the numbers a user acts on.

    Rate-driven components are excluded from every total. Their coefficients
    are meaningless -- the energy rate moved several-fold over the history, so
    fitting it against kWh yields a negative slope and a large spurious
    per-day term that would otherwise be banked as a standing charge.

    ``standing_fixed`` sums every intercept, not only the rows that look
    constant. The intercept is part of the fitted model regardless of how much
    the row varies, and skipping the ones that vary silently drops real fixed
    charges such as the admin fee.
    """
    by_key = {c.key: c for c in COMPONENTS}

    def usable(key: str) -> bool:
        comp = by_key[key]
        return comp.marginal_import and not comp.rate_driven

    return {
        "variable": sum(f["per_kwh"] for k, f in fits.items() if usable(k)),
        "standing_day": sum(f["per_day"] for k, f in fits.items() if usable(k)),
        "standing_fixed": sum(f["fixed"] for k, f in fits.items() if usable(k)),
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

    def step():
        nonlocal year, month
        month -= 1
        if month == 0:
            year, month = year - 1, 12

    for _ in range(months):
        period = f"{year:04d}{month:02d}"
        pdf_path = outdir / f"consolidated-{period}.pdf"

        if pdf_path.exists():
            pdf, note = pdf_path.read_bytes(), "cached"
        else:
            try:
                pdf = client.consolidated(period)
            except DocumentUnavailable:
                print(f"  {period}  {'-':>8}")
                step()
                continue
            pdf_path.write_bytes(pdf)
            note = f"{len(pdf)}B"
            # Be a considerate guest: this is someone else's billing system.
            time.sleep(delay)

        statement = parse(pdf, period)
        if not statement.values:
            # Periods before the account existed come back as a stub PDF
            # rather than a 404, so an empty parse means "no statement", not
            # "the layout changed".
            print(f"  {period}  {note:>8}  no statement (before account start?)")
            step()
            continue

        records.append(statement.as_dict())
        rec = records[-1]
        print(
            f"  {period}  {note:>8}  {rec.get(IMPORTED, '?')} kWh in, "
            f"{rec.get(EXPORTED, '?')} kWh out, ${rec.get('amount_due', '?')}"
        )
        step()

    return records


def report(records: list[dict]) -> None:
    print(f"\n{len(records)} statements parsed\n")
    print("=" * 78)
    print("Cost structure   charge ~ fixed + per_unit*volume + per_day*days")
    print("=" * 78)
    print(f"{'component':<32}{'on':>4}{'fixed':>9}{'$/unit':>10}{'$/day':>9}{'r2':>8}{'n':>5}")
    print("-" * 78)

    fits: dict[str, dict] = {}
    for comp in COMPONENTS:
        result = fit(records, comp.key, comp.predictor)
        if result is None:
            print(f"{comp.key:<32}{'insufficient data':>46}")
            continue
        fits[comp.key] = result
        flag = " ~const" if result["nearly_constant"] else ""
        tag = "exp" if comp.predictor == EXPORTED else "imp"
        print(
            f"{comp.key:<32}{tag:>4}{result['fixed']:>9.2f}{result['per_kwh']:>10.5f}"
            f"{result['per_day']:>9.3f}{result['r2']:>8.3f}{result['n']:>5}{flag}"
        )
    print("-" * 78)

    rates = sorted({r["rate_cents_per_kwh"] for r in records
                    if r.get("rate_cents_per_kwh")})
    latest = next((r.get("rate_cents_per_kwh") for r in records
                   if r.get("rate_cents_per_kwh")), None)

    totals = summarise(fits)
    variable = totals["variable"]
    standing_day = totals["standing_day"]
    standing_fixed = totals["standing_fixed"]

    print("\nPRICES FOR HOME ASSISTANT")
    print(f"  wires and fees, per imported kWh   ${variable:.5f}")
    if latest is not None:
        energy = latest / 100.0
        print(f"  energy rate (from the bill)        ${energy:.5f}")
        print(f"  marginal import price              ${energy + variable:.5f}/kWh"
              f"   (${(energy + variable) * 1.05:.5f} incl GST)")
        print(f"  export price                       ${energy:.5f}/kWh"
              "   (microgen is credited at the energy rate)")
    print(f"\n  standing charges  ${standing_day:.3f}/day + ${standing_fixed:.2f}/month"
          f"  ~ ${standing_day * 30 + standing_fixed:.2f}/month")
    print("  These cannot be expressed as a price per kWh; track them separately.")

    if latest is not None:
        recent = records[0]
        kwh_in = recent.get(IMPORTED) or 0
        kwh_out = recent.get(EXPORTED) or 0
        days = recent.get("service_days") or 30
        energy = latest / 100.0
        predicted = ((energy + variable) * kwh_in + standing_day * days
                     + standing_fixed - energy * kwh_out)
        actual = recent.get("charges_before_tax")
        if actual:
            err = predicted - actual
            print(f"\n  check against {recent['period']}: predicted ${predicted:.2f}"
                  f" vs actual ${actual:.2f}  ({err:+.2f}, {abs(err) / actual:.1%})")

    print("\nNOTES")
    print("  r2 near zero on a row marked ~const is expected and fine: a charge")
    print("  that barely moves has little variance to explain. Distrust a low r2")
    print("  only on rows that do vary.")
    if len(rates) > 1:
        print(f"  energy rates seen: {rates} cents/kWh -- rate-driven rows are read")
        print("  from the bill, not fitted, so this does not distort the prices.")


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

    Path("history.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print("\nwrote history.json")

    report(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
