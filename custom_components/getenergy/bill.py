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

# A currency amount. Credits print either parenthesised, with a leading minus,
# or with a trailing CR; the capture is always the magnitude.
_MONEY = r"\(?-?\$?\s*([\d,]+\.\d{2})\)?(?:\s*CR\b)?"

_NUM = r"([\d,]+(?:\.\d+)?)"


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
                # Parenthesised or CR-suffixed amounts are credits.
                if re.search(r"\)\s*$|CR\b", match.group(0), re.IGNORECASE):
                    value = -value
                values.append(value)

            if not values:
                continue
            return sum(values) if self.aggregate == "sum" else values[0]
        return None


FIELDS: tuple[Field, ...] = (
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
    Field(
        "previous_balance",
        (rf"previous\s+balance{_SEP}{_MONEY}",),
        "CAD",
    ),
    Field(
        # Printed as a negative because it reduces the balance; exposed as the
        # positive amount paid, which is how the label reads.
        "payments",
        (
            rf"total\s+payments{_SEP}{_MONEY}",
            rf"payments\s+received{_SEP}{_MONEY}",
            rf"payments{_SEP}{_MONEY}",
        ),
        "CAD",
    ),
    Field(
        "balance_forward",
        (rf"balance\s+forward{_SEP}{_MONEY}",),
        "CAD",
    ),
    Field(
        # The subtotal before tax. Anchored on "Subtotal" first because
        # "Total Charges" also begins "Total Charges and GST".
        "charges_before_tax",
        (rf"subtotal{_SEP}{_MONEY}", rf"total\s+charges{_SEP}{_MONEY}"),
        "CAD",
    ),
    Field(
        "gst",
        (rf"total\s+gst{_SEP}{_MONEY}", rf"\bgst{_SEP}{_MONEY}"),
        "CAD",
    ),
    Field(
        # "Usage: 560 kWh" is the billed total. The per-period energy charge
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
        # Exported solar shows up only as the microgen credit lines, one per
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


@dataclass
class Statement:
    """Figures extracted from one statement PDF."""

    period: str
    values: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def statement_date(self) -> date:
        return datetime.strptime(self.period + "01", "%Y%m%d").date()

    def as_dict(self) -> dict[str, Any]:
        return {"period": self.period, **self.values}


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
    statement = Statement(period=period)

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
