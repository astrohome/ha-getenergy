"""Extract figures from a Get Energy statement PDF.

Why parse the PDF rather than the RPC payload? The portal's only read method,
``getPortalLoader``, returns a positional GWT-RPC stream whose field schema
lives solely in obfuscated compiled JavaScript. That schema changes on every
vendor redeploy, so a positional decoder would keep parsing happily while
silently attaching the wrong number to the wrong sensor.

The bill is the opposite: its labels are human-facing, so they change rarely
and visibly. Every field here is therefore anchored on a printed label, and
anything that does not match is reported as ``None`` -- an unavailable sensor
is a correct answer, a wrong number is not.

Labels are best-effort across statement layouts. Run ``tools/calibrate.py``
against a real statement to see the extracted text and which patterns hit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)

# A currency amount. Credits are printed either parenthesised or with a
# trailing CR, so both markers are matched here rather than after the capture
# -- otherwise they fall outside group(0) and the sign is lost.
_MONEY = r"\(?-?\$?\s*([\d,]+\.\d{2})\)?(?:\s*CR\b)?"
_NUMBER = r"(-?[\d,]+(?:\.\d+)?)"


def _money(raw: str, negative: bool = False) -> float:
    value = float(raw.replace(",", ""))
    return -value if negative else value


@dataclass(frozen=True)
class Field:
    """One extractable figure, anchored on the label printed on the bill."""

    key: str
    patterns: tuple[str, ...]
    unit: str
    kind: str = "money"

    def search(self, text: str) -> float | None:
        for pattern in self.patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            raw = match.group(1)
            # A trailing CR, or parentheses, denotes a credit.
            negative = bool(re.search(r"\)\s*$|CR\b", match.group(0), re.IGNORECASE))
            try:
                return _money(raw, negative) if self.kind == "money" else float(
                    raw.replace(",", "")
                )
            except ValueError:
                _LOGGER.debug("unparseable value for %s: %r", self.key, raw)
        return None


FIELDS: tuple[Field, ...] = (
    Field(
        "amount_due",
        (
            rf"total\s+amount\s+due[^\d\-\(]{{0,40}}{_MONEY}",
            rf"amount\s+due[^\d\-\(]{{0,40}}{_MONEY}",
            rf"please\s+pay[^\d\-\(]{{0,40}}{_MONEY}",
        ),
        "CAD",
    ),
    Field(
        "previous_balance",
        (rf"previous\s+balance[^\d\-\(]{{0,40}}{_MONEY}",
         rf"balance\s+forward[^\d\-\(]{{0,40}}{_MONEY}"),
        "CAD",
    ),
    Field(
        "payments",
        (rf"payment(?:s)?\s+received[^\d\-\(]{{0,40}}{_MONEY}",
         rf"total\s+payments[^\d\-\(]{{0,40}}{_MONEY}"),
        "CAD",
    ),
    Field(
        "current_charges",
        (rf"current\s+charges[^\d\-\(]{{0,40}}{_MONEY}",
         rf"total\s+current\s+charges[^\d\-\(]{{0,40}}{_MONEY}"),
        "CAD",
    ),
    Field(
        "electricity_imported_kwh",
        (rf"imported[^\d\-]{{0,40}}{_NUMBER}\s*kWh",
         rf"energy\s+imported[^\d\-]{{0,40}}{_NUMBER}",
         rf"total\s+consumption[^\d\-]{{0,40}}{_NUMBER}\s*kWh"),
        "kWh",
        kind="number",
    ),
    Field(
        "electricity_exported_kwh",
        (rf"exported[^\d\-]{{0,40}}{_NUMBER}\s*kWh",
         rf"energy\s+exported[^\d\-]{{0,40}}{_NUMBER}",
         rf"micro[- ]?gen(?:eration)?[^\d\-]{{0,40}}{_NUMBER}\s*kWh"),
        "kWh",
        kind="number",
    ),
    Field(
        "gas_consumption_gj",
        (rf"{_NUMBER}\s*GJ\b", rf"consumption[^\d\-]{{0,40}}{_NUMBER}\s*GJ"),
        "GJ",
        kind="number",
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
    """Return the text of every page, whitespace-normalised.

    Raises ``ImportError`` with an actionable message if pypdf is missing.
    """
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
