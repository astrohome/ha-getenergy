"""Turn a statement into the prices Home Assistant can actually use.

The energy dashboard computes ``cost = sum(delta_kWh * price)``. That is a
pure multiplication, so a price entity can carry a *marginal* rate and nothing
else. Standing charges -- the admin fee and the fixed part of the distribution
charge -- cannot be expressed per kWh at all.

Two pricing schemes are possible and they are mutually exclusive:

* **Marginal** (used here). The price of the next kWh. Correct for deciding
  whether to run something now, and correct for the variable portion of the
  bill. The dashboard total will fall short of the bill by the standing
  charges, which are exposed separately so they can be added back.

* **Average** (running total / kWh so far). Reconciles to the bill, but the
  marginal signal is meaningless and it spikes absurdly early in the month
  when a month of fixed charges is divided by a handful of kWh.

The energy rate itself is read from the bill rather than assumed: this account
is on a seasonal Solar Club rate that auto-switches, so it moves between
roughly 6.9 and 35 cents over the year.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bill import Statement


@dataclass(frozen=True)
class Coefficients:
    """The parts of the bill that are not the energy rate."""

    wires_per_kwh: float
    standing_per_day: float
    standing_per_month: float
    gst_rate: float


@dataclass(frozen=True)
class Prices:
    """Prices in dollars, GST included unless the rate is zero."""

    energy_rate: float | None
    import_price: float | None
    export_price: float | None
    standing_per_day: float
    standing_per_month: float

    @property
    def available(self) -> bool:
        return self.energy_rate is not None


def energy_rate(statement: Statement | None) -> float | None:
    """The current energy rate in dollars per kWh.

    Prefers the "Current Rate" line, which states the rate in force going
    forward. Falls back to the highest rate among the billed segments: a
    statement spanning a seasonal switch carries both the old and the new
    rate, and the Solar Club switches *up* into the high-rate season, so the
    larger value is the one still in effect.

    Returns None rather than guessing when the statement shows neither.
    """
    if statement is None:
        return None

    cents = statement.meta.get("rate_cents_per_kwh")
    if cents:
        return round(cents / 100.0, 6)

    rates = [r for r in statement.energy_rates if r > 0]
    return max(rates) if rates else None


def compute(statement: Statement | None, coeffs: Coefficients) -> Prices:
    """Derive import and export prices from a statement."""
    rate = energy_rate(statement)
    gst = 1.0 + coeffs.gst_rate

    if rate is None:
        return Prices(
            energy_rate=None,
            import_price=None,
            export_price=None,
            standing_per_day=coeffs.standing_per_day,
            standing_per_month=coeffs.standing_per_month,
        )

    return Prices(
        energy_rate=rate,
        # Energy plus every per-kWh wires and fee component.
        import_price=round((rate + coeffs.wires_per_kwh) * gst, 6),
        # Microgen is credited at the energy rate, with no wires component --
        # exporting does not incur transmission or distribution charges.
        export_price=round(rate * gst, 6),
        standing_per_day=coeffs.standing_per_day,
        standing_per_month=coeffs.standing_per_month,
    )


def monthly_standing_charge(coeffs: Coefficients, days: int = 30) -> float:
    """What the price entities structurally cannot capture, per month."""
    return round(
        (coeffs.standing_per_day * days + coeffs.standing_per_month)
        * (1.0 + coeffs.gst_rate),
        2,
    )
