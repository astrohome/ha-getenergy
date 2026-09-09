"""Tests for building long-term statistics from past statements."""

from __future__ import annotations

from datetime import date, timezone

import pytest

from getenergy.bill import Statement
from getenergy.pricing import Coefficients
from getenergy.statistics import build, daily_values, metadata, statistic_id

COEFFS = Coefficients(
    wires_per_kwh=0.10025,
    standing_per_day=1.140,
    standing_per_month=10.22,
    gst_rate=0.05,
)
UTC = timezone.utc


def _statement(period, first, last, imported, exported, rate=35.0) -> Statement:
    days = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    return Statement(
        period=period,
        values={
            "electricity_imported_kwh": imported,
            "electricity_exported_kwh": exported,
        },
        meta={
            "service_from": first,
            "service_to": last,
            "service_days": days,
            "rate_cents_per_kwh": rate,
        },
    )


# Two consecutive periods, the first straddling a month boundary.
STATEMENTS = [
    _statement("202608", "2026-06-23", "2026-07-22", 300.0, 600.0),
    _statement("202609", "2026-07-23", "2026-08-21", 600.0, 300.0),
]


def test_energy_is_spread_evenly_across_the_service_window():
    by_day = daily_values(STATEMENTS, COEFFS)["electricity_imported"]

    assert len(by_day) == 60
    assert by_day[date(2026, 6, 23)] == pytest.approx(10.0)   # 300 over 30 days
    assert by_day[date(2026, 7, 23)] == pytest.approx(20.0)   # 600 over 30 days


def test_a_period_straddling_months_lands_in_both_calendar_months():
    """This is why the total is spread rather than stamped on one day.

    Stamping a whole period on its start date would put every kWh of a
    Jun 23 - Jul 22 window into June, making monthly dashboard totals wrong.
    """
    by_day = daily_values(STATEMENTS, COEFFS)["electricity_imported"]

    june = sum(v for d, v in by_day.items() if d.month == 6)
    july = sum(v for d, v in by_day.items() if d.month == 7)

    august = sum(v for d, v in by_day.items() if d.month == 8)

    assert june == pytest.approx(80.0)               # Jun 23-30: 8 days at 10
    assert july == pytest.approx(220.0 + 180.0)      # Jul 1-22 at 10, 23-31 at 20
    assert august == pytest.approx(420.0)            # Aug 1-21: 21 days at 20
    assert june + july + august == pytest.approx(900.0)   # nothing lost overall


def test_sums_are_cumulative_and_monotonic():
    """The recorder differences consecutive sums to get each bucket."""
    rows = build(STATEMENTS, COEFFS, UTC)["electricity_imported"]

    sums = [r["sum"] for r in rows]
    assert sums == sorted(sums)
    assert sums[-1] == pytest.approx(900.0)


def test_rows_are_ordered_and_hour_aligned():
    rows = build(STATEMENTS, COEFFS, UTC)["electricity_exported"]

    starts = [r["start"] for r in rows]
    assert starts == sorted(starts)
    assert all(s.minute == 0 and s.second == 0 and s.hour == 0 for s in starts)
    assert all(s.tzinfo is not None for s in starts)


def test_cost_series_uses_each_statement_own_rate():
    """A backfill spans rate changes, so cost must not use today's rate."""
    cheap = _statement("202510", "2025-09-23", "2025-10-22", 300.0, 0.0, rate=6.89)
    dear = _statement("202511", "2025-10-23", "2025-11-21", 300.0, 0.0, rate=35.0)

    by_day = daily_values([cheap, dear], COEFFS)["import_cost"]

    assert by_day[date(2025, 9, 23)] < by_day[date(2025, 10, 23)]


def test_statement_without_a_service_window_is_skipped():
    """Placing energy on guessed dates is worse than omitting it."""
    orphan = Statement(period="202607", values={"electricity_imported_kwh": 500.0})

    by_day = daily_values([*STATEMENTS, orphan], COEFFS)["electricity_imported"]

    assert sum(by_day.values()) == pytest.approx(900.0)


def test_overlapping_windows_do_not_double_count():
    """A re-issued bill covering the same days must replace, not add."""
    reissued = _statement("202609", "2026-07-23", "2026-08-21", 900.0, 300.0)

    by_day = daily_values([*STATEMENTS, reissued], COEFFS)["electricity_imported"]

    assert by_day[date(2026, 7, 23)] == pytest.approx(30.0)   # 900/30, not 20+30
    assert sum(by_day.values()) == pytest.approx(300.0 + 900.0)


def test_missing_figure_omits_only_that_series():
    no_export = Statement(
        period="202609",
        values={"electricity_imported_kwh": 300.0},
        meta={"service_from": "2026-07-23", "service_to": "2026-08-21",
              "rate_cents_per_kwh": 35.0},
    )
    series = daily_values([no_export], COEFFS)

    assert series["electricity_imported"]
    assert series["electricity_exported"] == {}
    assert series["export_credit"] == {}


def test_statistic_ids_are_namespaced():
    assert statistic_id("electricity_imported") == "getenergy:electricity_imported"


def test_metadata_declares_a_sum_series():
    meta = metadata("electricity_imported")

    assert meta["has_sum"] is True
    assert meta["has_mean"] is False
    assert meta["source"] == "getenergy"
    assert meta["unit_of_measurement"] == "kWh"


def test_metadata_declares_mean_type_when_the_core_supports_it():
    """has_mean is deprecated and stops working in Home Assistant 2026.11.

    Without Home Assistant installed the enum cannot be imported, so the key
    is omitted rather than faked -- but has_sum must always be declared, since
    these are pure running totals.
    """
    from getenergy.statistics import _mean_type_none

    meta = metadata("electricity_imported")

    assert meta["has_sum"] is True
    assert meta["has_mean"] is False
    if _mean_type_none() is not None:
        assert "mean_type" in meta
    else:
        assert "mean_type" not in meta


def test_mean_type_helper_tolerates_a_missing_core():
    """Must degrade quietly, not raise, when recorder is unavailable."""
    from getenergy.statistics import _mean_type_none

    _mean_type_none()  # no exception
