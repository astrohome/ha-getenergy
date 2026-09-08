"""Tests for deriving Home Assistant prices from a statement."""

from __future__ import annotations

import pytest

from getenergy.bill import Statement
from getenergy.pricing import Coefficients, compute, energy_rate, monthly_standing_charge

# Fitted from a real Alberta microgeneration account.
COEFFS = Coefficients(
    wires_per_kwh=0.10025,
    standing_per_day=1.140,
    standing_per_month=10.22,
    gst_rate=0.05,
)


def _statement(**meta) -> Statement:
    return Statement(period="202609", meta=meta)


def test_import_price_is_energy_plus_wires_with_gst():
    prices = compute(_statement(rate_cents_per_kwh=35.0), COEFFS)

    assert prices.energy_rate == 0.35
    assert prices.import_price == pytest.approx((0.35 + 0.10025) * 1.05, abs=1e-6)


def test_export_price_carries_no_wires_component():
    """Exporting incurs no transmission or distribution charge."""
    prices = compute(_statement(rate_cents_per_kwh=35.0), COEFFS)

    assert prices.export_price == pytest.approx(0.35 * 1.05, abs=1e-6)
    assert prices.export_price < prices.import_price


def test_prices_follow_the_seasonal_rate_switch():
    """The Solar Club rate moves between roughly 6.9 and 35 cents."""
    low = compute(_statement(rate_cents_per_kwh=6.89), COEFFS)
    high = compute(_statement(rate_cents_per_kwh=35.0), COEFFS)

    assert low.import_price < high.import_price
    assert low.export_price == pytest.approx(0.0689 * 1.05)


def test_rate_falls_back_to_the_highest_billed_segment():
    """A statement spanning a switch carries both rates.

    The Solar Club switches up into the high-rate season, so the larger of the
    two is the one still in force.
    """
    statement = Statement(
        period="202509",
        segments=[
            {"kind": "energy charge", "rate": 0.33, "kwh": 433.0, "amount": 142.89},
            {"kind": "energy charge", "rate": 0.0689, "kwh": 16.0, "amount": 1.10},
        ],
    )
    assert energy_rate(statement) == 0.33


def test_no_rate_yields_no_price_rather_than_a_guess():
    """A wrong price is multiplied by every kWh, so silence is safer."""
    prices = compute(_statement(), COEFFS)

    assert prices.available is False
    assert prices.import_price is None
    assert prices.export_price is None
    # Standing charges are configured, not derived, so they survive.
    assert prices.standing_per_day == 1.140


def test_missing_statement_is_handled():
    assert compute(None, COEFFS).available is False


def test_standing_charge_is_what_the_price_entities_cannot_express():
    assert monthly_standing_charge(COEFFS, days=30) == pytest.approx(46.63, abs=0.01)


def test_zero_gst_leaves_prices_untaxed():
    coeffs = Coefficients(0.10025, 1.140, 10.22, gst_rate=0.0)
    prices = compute(_statement(rate_cents_per_kwh=35.0), coeffs)

    assert prices.import_price == pytest.approx(0.45025, abs=1e-6)
