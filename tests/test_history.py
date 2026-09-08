"""Tests for the cost-structure regression in tools/history.py.

The whole point of the fit is to separate fixed monthly charges from per-kWh
ones, because Home Assistant can only price the latter. If the solver were
subtly wrong it would still return confident-looking numbers, so these tests
build statements from a known structure and check it comes back.
"""

from __future__ import annotations

import random

from history import fit, r_squared, solve

FIXED, PER_KWH, PER_DAY = 12.50, 0.0455, 0.3100


def _statements(count: int, noise: float = 0.0, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        kwh = rng.uniform(150, 1400)
        days = rng.choice([28, 29, 30, 31, 32])
        charge = FIXED + PER_KWH * kwh + PER_DAY * days
        if noise:
            charge += rng.uniform(-noise, noise)
        out.append(
            {
                "electricity_imported_kwh": kwh,
                "service_days": days,
                "distribution_charge": round(charge, 2),
            }
        )
    return out


def test_recovers_a_known_cost_structure():
    result = fit(_statements(18), "distribution_charge")

    assert result["n"] == 18
    assert abs(result["fixed"] - FIXED) < 0.1
    assert abs(result["per_kwh"] - PER_KWH) < 1e-4
    assert abs(result["per_day"] - PER_DAY) < 0.01
    assert result["r2"] > 0.999


def test_noise_lowers_r2_without_destroying_the_slope():
    result = fit(_statements(24, noise=3.0), "distribution_charge")

    assert result["r2"] < 0.999
    assert abs(result["per_kwh"] - PER_KWH) < 0.01


def test_refuses_when_kwh_never_varies():
    """No variation cannot separate a fixed charge from a per-kWh one.

    Returning a plausible-looking split here would be worse than returning
    nothing: it would be priced into a cost sensor and silently believed.
    """
    flat = [
        {
            "electricity_imported_kwh": 500,
            "service_days": 30,
            "distribution_charge": 40.0,
        }
        for _ in range(6)
    ]
    assert fit(flat, "distribution_charge") is None


def test_refuses_with_too_few_statements():
    assert fit(_statements(3), "distribution_charge") is None


def test_skips_statements_missing_a_figure():
    records = _statements(6)
    records[0].pop("distribution_charge")
    records[1].pop("service_days")

    assert fit(records, "distribution_charge")["n"] == 4


def test_solve_reports_singular_systems():
    assert solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0]) is None


def test_r_squared_is_one_for_a_perfect_fit():
    assert r_squared([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
