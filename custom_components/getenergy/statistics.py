"""Turn past statements into Home Assistant long-term statistics.

The energy dashboard reads from the statistics tables, so historical bills can
be inserted directly as *external* statistics and show up alongside live data.

**Each period's kWh is spread evenly across the days it covers.** The bill only
gives a total for the whole meter-read window, so the intra-period shape is
uniform and not real -- that is a deliberate approximation and is documented in
the README.

Spreading is not cosmetic. Service periods straddle calendar months (Jul 23 to
Aug 21, say), so stamping a period's whole total on one day would attribute it
entirely to the wrong month and make the dashboard's monthly totals wrong.
Distributing over the actual days puts each kWh in the calendar month it
belongs to.

Statistics carry a *cumulative* sum, so the series is built oldest-first and
every value is a running total. Home Assistant derives each bucket's usage by
differencing consecutive sums.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from .bill import Statement
from .pricing import Coefficients, compute

_LOGGER = logging.getLogger(__name__)

DOMAIN_PREFIX = "getenergy"

#: statistic key -> (human name, unit)
ENERGY_SERIES: dict[str, tuple[str, str]] = {
    "electricity_imported": ("Electricity imported", "kWh"),
    "electricity_exported": ("Electricity exported", "kWh"),
}
COST_SERIES: dict[str, tuple[str, str]] = {
    "import_cost": ("Electricity import cost", "CAD"),
    "export_credit": ("Electricity export credit", "CAD"),
}
ALL_SERIES = {**ENERGY_SERIES, **COST_SERIES}


def statistic_id(key: str) -> str:
    """External statistic ids are namespaced with a colon."""
    return f"{DOMAIN_PREFIX}:{key}"


def _service_window(statement: Statement) -> tuple[date, date] | None:
    start, end = statement.meta.get("service_from"), statement.meta.get("service_to")
    if not start or not end:
        return None
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return (first, last) if last >= first else None


def daily_values(
    statements: list[Statement], coeffs: Coefficients
) -> dict[str, dict[date, float]]:
    """Spread every statement across its days, keyed by series then date.

    Statements whose service window is unknown are skipped rather than guessed
    at: placing energy on the wrong dates is worse than omitting it, because
    the dashboard would show usage in a month it did not occur.

    Where windows overlap, the later statement wins. Overlap means a corrected
    or re-issued bill, and the newer figure is the one to trust.
    """
    out: dict[str, dict[date, float]] = {key: {} for key in ALL_SERIES}

    for statement in sorted(statements, key=lambda s: s.period):
        window = _service_window(statement)
        if window is None:
            _LOGGER.debug("statement %s has no service window; skipped", statement.period)
            continue

        first, last = window
        days = (last - first).days + 1
        prices = compute(statement, coeffs)

        imported = statement.values.get("electricity_imported_kwh")
        exported = statement.values.get("electricity_exported_kwh")

        per_day = {
            "electricity_imported": (imported / days) if imported is not None else None,
            "electricity_exported": (exported / days) if exported is not None else None,
        }
        per_day["import_cost"] = (
            per_day["electricity_imported"] * prices.import_price
            if per_day["electricity_imported"] is not None
            and prices.import_price is not None
            else None
        )
        per_day["export_credit"] = (
            per_day["electricity_exported"] * prices.export_price
            if per_day["electricity_exported"] is not None
            and prices.export_price is not None
            else None
        )

        for offset in range(days):
            day = first + timedelta(days=offset)
            for key, value in per_day.items():
                if value is not None:
                    out[key][day] = value

    return out


def build(
    statements: list[Statement], coeffs: Coefficients, zone: tzinfo
) -> dict[str, list[dict[str, Any]]]:
    """Build cumulative statistic rows per series.

    Timestamps are local midnight, which is hour-aligned as the recorder
    requires. Returns plain dicts so this module stays importable -- and
    testable -- without Home Assistant.
    """
    series: dict[str, list[dict[str, Any]]] = {}

    for key, by_day in daily_values(statements, coeffs).items():
        if not by_day:
            continue
        rows: list[dict[str, Any]] = []
        total = 0.0
        for day in sorted(by_day):
            total += by_day[day]
            rows.append(
                {
                    "start": datetime(day.year, day.month, day.day, tzinfo=zone),
                    "state": round(by_day[day], 4),
                    "sum": round(total, 4),
                }
            )
        series[key] = rows

    return series


def metadata(key: str) -> dict[str, Any]:
    """Recorder metadata for one series."""
    name, unit = ALL_SERIES[key]
    return {
        "has_mean": False,
        "has_sum": True,
        "name": f"Get Energy {name}",
        "source": DOMAIN_PREFIX,
        "statistic_id": statistic_id(key),
        "unit_of_measurement": unit,
    }
