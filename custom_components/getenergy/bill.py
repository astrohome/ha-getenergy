"""Extract figures from a Get Energy statement PDF.

Why parse the PDF rather than the RPC payload? The portal's only read method,
``getPortalLoader``, returns a positional GWT-RPC stream whose field schema
lives solely in obfuscated compiled JavaScript. That schema changes on every
vendor redeploy, so a positional decoder would keep parsing happily while
silently attaching the wrong number to the wrong sensor.

The bill is the opposite: its labels are human-facing, so they change rarely
and visibly. Every field here is anchored on a printed label, and anything that
does not match is reported as ``None`` -- an unavailable sensor is a correct
answer, a wrong number is not.

The separator between a label and its value deliberately allows only
whitespace, dot leaders and colons. A looser class lets a pattern stride across
intervening words: "Total Charges" would happily match the value in "Total
Charges and GST $210.00", reporting the post-tax total as the subtotal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Only whitespace, dot leaders and colons may sit between a label and its
# value. See the module docstring for why this must not be widened.
_SEP = r"[\s.:·]{0,80}"

# A currency amount; the capture is always the magnitude, sign handled below.
_MONEY = r"\(?-?\$?\s*([\d,]+\.\d{2})\)?(?:\s*CR\b)?"

_NUM = r"([\d,]+(?:\.\d+)?)"


def _is_credit(matched: str) -> bool:
    """Is this amount printed as a credit?

    Bills mark them three ways: a leading minus, parentheses, or a trailing
    CR. Transmission riders go negative in some months and positive in others,
    so the sign has to be read rather than assumed.
    """
    if re.search(r"-\s*\$?\s*[\d,]+\.\d{2}", matched):
        return True
    if re.search(r"\)\s*$", matched):
        return True
    # Word-bounded and case-sensitive on purpose: a loose /cr/i also matches
    # the "Cr" in "Microgen Credit", flipping the sign of every such line.
    return bool(re.search(r"\bCR\b", matched))


@dataclass(frozen=True)
class Field:
    """One extractable figure, anchored on the label printed on the bill."""

    key: str
    patterns: tuple[str, ...]
    unit: str
    kind: str = "money"
    #: "first" takes the earliest match; "sum" adds every match of the first
    #: pattern that hits, for figures the bill splits across meter periods.
    aggregate: str = "first"
    #: When True the printed sign is honoured, so riders and credits stay
    #: negative. When False the magnitude is returned -- right for figures the
    #: bill prints negative only because they reduce the balance, such as
    #: payments, where "you paid $16.00" is the natural reading.
    signed: bool = True

    def search(self, text: str) -> float | None:
        for pattern in self.patterns:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if not matches:
                continue

            values: list[float] = []
            for match in matches:
                try:
                    value = float(match.group(1).replace(",", ""))
                except ValueError:
                    _LOGGER.debug("unparseable value for %s", self.key)
                    continue
                if self.signed and _is_credit(match.group(0)):
                    value = -value
                values.append(value)

            if not values:
                continue
            return sum(values) if self.aggregate == "sum" else values[0]
        return None


#: Bill totals -- what the sensors expose.
SUMMARY_FIELDS: tuple[Field, ...] = (
    Field(
        "amount_due",
        (
            rf"current\s+due{_SEP}{_MONEY}",
            rf"total\s+due{_SEP}{_MONEY}",
            rf"total\s+current\s+charges{_SEP}{_MONEY}",
            rf"total\s+amount\s+due{_SEP}{_MONEY}",
        ),
        "CAD",
    ),
    Field("previous_balance", (rf"previous\s+balance{_SEP}{_MONEY}",), "CAD"),
    Field(
        # Printed negative because it reduces the balance; exposed as the
        # positive amount paid, which is how the label reads.
        "payments",
        (
            rf"total\s+payments{_SEP}{_MONEY}",
            rf"payments\s+received{_SEP}{_MONEY}",
            rf"payments{_SEP}{_MONEY}",
        ),
        "CAD",
        signed=False,
    ),
    Field("balance_forward", (rf"balance\s+forward{_SEP}{_MONEY}",), "CAD"),
    Field(
        # Anchored on "Subtotal" first because "Total Charges" also begins
        # "Total Charges and GST".
        "charges_before_tax",
        (rf"subtotal{_SEP}{_MONEY}", rf"total\s+charges{_SEP}{_MONEY}"),
        "CAD",
    ),
    Field("gst", (rf"total\s+gst{_SEP}{_MONEY}", rf"\bgst{_SEP}{_MONEY}"), "CAD"),
    Field(
        # "Usage: 400 kWh" is the billed total; the per-period energy charge
        # lines are summed as a fallback and agree with it.
        "electricity_imported_kwh",
        (
            rf"usage{_SEP}{_NUM}\s*kWh",
            rf"energy\s+charge\s*\([^)]*?:\s*{_NUM}\s*kWh",
        ),
        "kWh",
        kind="number",
        aggregate="sum",
    ),
    Field(
        # Exported solar appears only as microgen credit lines, one per
        # meter-read period, so they must be summed.
        "electricity_exported_kwh",
        (
            rf"microgen\s+credit\s*\([^)]*?:\s*{_NUM}\s*kWh",
            rf"micro\s+generation[^$]*?total{_SEP}{_NUM}\b",
        ),
        "kWh",
        kind="number",
        aggregate="sum",
    ),
    Field(
        "gas_consumption_gj",
        (rf"usage{_SEP}{_NUM}\s*GJ", rf"{_NUM}\s*GJ\b"),
        "GJ",
        kind="number",
        aggregate="sum",
    ),
)

#: Itemised charge components, used to separate fixed from per-kWh costs.
#: Each label is followed directly by its total, so the strict separator also
#: keeps "Energy" from matching the per-period "Energy Charge ( ... )" lines.
COMPONENT_FIELDS: tuple[Field, ...] = (
    Field("energy_charge", (rf"\benergy{_SEP}{_MONEY}",), "CAD"),
    Field(
        "green_offset_charge",
        (rf"green\s+offset\s+charge\s*\([^)]*\){_SEP}{_MONEY}",),
        "CAD",
        aggregate="sum",
    ),
    Field("microgen_credit", (rf"\bmicrogen{_SEP}{_MONEY}",), "CAD"),
    Field(
        "transmission_and_distribution",
        (rf"regulated\s+transmission\s+and\s+distribution{_SEP}{_MONEY}",),
        "CAD",
    ),
    Field("transmission_charge", (rf"transmission\s+charge{_SEP}{_MONEY}",), "CAD"),
    Field("transmission_riders", (rf"transmission\s+riders{_SEP}{_MONEY}",), "CAD"),
    Field("distribution_charge", (rf"distribution\s+charge{_SEP}{_MONEY}",), "CAD"),
    Field("distribution_riders", (rf"distribution\s+riders{_SEP}{_MONEY}",), "CAD"),
    Field(
        "balancing_pool",
        (
            rf"balancing\s+pool\s+charge{_SEP}{_MONEY}",
            rf"balancing\s+pool\s+allocation{_SEP}{_MONEY}",
        ),
        "CAD",
    ),
    Field(
        # The detail page calls it "Franchise Fee"; the summary calls it
        # "Municipal Fee to <city>", so that variant has to cross the city
        # name -- bounded, and stopped at a newline, to limit the reach.
        "franchise_fee",
        (
            rf"franchise\s+fee{_SEP}{_MONEY}",
            rf"municipal\s+fee[^\n$]{{0,40}}{_MONEY}",
        ),
        "CAD",
    ),
    Field(
        "admin_charge",
        (rf"admin\s+charge{_SEP}{_MONEY}", rf"retailer\s+fees{_SEP}{_MONEY}"),
        "CAD",
    ),
)

FIELDS: tuple[Field, ...] = SUMMARY_FIELDS + COMPONENT_FIELDS

# "Jul 01 2026 - Jul 30 2026 Usage: 400 kWh"
SERVICE_PERIOD = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4})\s*-\s*([A-Z][a-z]{2}\s+\d{1,2}\s+\d{4})"
    r"\s*Usage",
)
STATEMENT_DATE = re.compile(r"Statement Date:\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})")
DUE_DATE = re.compile(r"Due Date:\s*([A-Z][a-z]{2}\s+\d{1,2},\s*\d{4})")
# "Current Rate: Fixed 35.000 cents / kWh effective for meter reads from ..."
RATE = re.compile(r"Current Rate:\s*(\w+)\s+([\d.]+)\s*cents\s*/\s*kWh", re.IGNORECASE)


def _date(raw: str) -> str | None:
    for fmt in ("%b %d %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(re.sub(r"\s+", " ", raw.strip()), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_meta(text: str) -> dict[str, Any]:
    """Pull the service period, key dates and the energy rate.

    The service period matters more than the statement date: it is the window
    the kWh figures actually cover, which is what any backfill of historical
    statistics has to be anchored on.
    """
    meta: dict[str, Any] = {}

    if (match := SERVICE_PERIOD.search(text)) is not None:
        meta["service_from"] = _date(match.group(1))
        meta["service_to"] = _date(match.group(2))
        if meta["service_from"] and meta["service_to"]:
            start = date.fromisoformat(meta["service_from"])
            end = date.fromisoformat(meta["service_to"])
            meta["service_days"] = (end - start).days + 1

    if (match := STATEMENT_DATE.search(text)) is not None:
        meta["statement_date"] = _date(match.group(1))
    if (match := DUE_DATE.search(text)) is not None:
        meta["due_date"] = _date(match.group(1))
    if (match := RATE.search(text)) is not None:
        meta["rate_type"] = match.group(1).lower()
        meta["rate_cents_per_kwh"] = float(match.group(2))

    return meta


@dataclass
class Statement:
    """Figures extracted from one statement PDF."""

    period: str
    values: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def statement_date(self) -> date:
        return datetime.strptime(self.period + "01", "%Y%m%d").date()

    def as_dict(self) -> dict[str, Any]:
        return {"period": self.period, **self.meta, **self.values}


def extract_text(pdf: bytes) -> str:
    """Return the text of every page, whitespace-normalised."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ImportError(
            "pypdf is required to read Get Energy statements; it is declared "
            "in the integration manifest."
        ) from exc

    import io

    reader = PdfReader(io.BytesIO(pdf))
    pages = [page.extract_text() or "" for page in reader.pages]
    return re.sub(r"[ \t]+", " ", "\n".join(pages))


def parse(pdf: bytes, period: str) -> Statement:
    """Extract every known field from a statement.

    Fields whose label is absent are recorded in ``missing`` rather than
    guessed at, so a layout change surfaces as unavailable sensors.
    """
    text = extract_text(pdf)
    statement = Statement(period=period, meta=extract_meta(text))

    for spec in FIELDS:
        value = spec.search(text)
        if value is None:
            statement.missing.append(spec.key)
        else:
            statement.values[spec.key] = value

    if statement.missing:
        _LOGGER.debug(
            "statement %s: no label matched for %s",
            period,
            ", ".join(statement.missing),
        )
    if not statement.values:
        _LOGGER.warning(
            "statement %s: no known labels matched at all. The bill layout has "
            "probably changed - run tools/calibrate.py to inspect it.",
            period,
        )
    return statement
