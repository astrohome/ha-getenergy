"""The ``getenergy.backfill`` service: import past statements as statistics.

Deliberately a service rather than something that runs on every setup. A
backfill downloads one PDF per month from the retailer's document service, and
re-fetching two years of bills every time Home Assistant restarts would be a
poor way to treat someone else's billing system.

Re-running is safe: statistics are keyed by statistic id and timestamp, so
importing the same period twice overwrites rather than doubles.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .api import AuthError, DocumentUnavailable, GetEnergyClient
from .bill import Statement, parse
from .const import BACKFILL_MONTHS, DOMAIN
from .pricing import Coefficients
from .statistics import build, metadata

_LOGGER = logging.getLogger(__name__)

SERVICE_BACKFILL = "backfill"
ATTR_MONTHS = "months"

# Courtesy delay between document downloads.
FETCH_DELAY = 1.5

SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_MONTHS, default=BACKFILL_MONTHS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=120)
        ),
    }
)


def collect(client: GetEnergyClient, months: int) -> list[Statement]:
    """Download and parse up to ``months`` of statements, newest first.

    Blocking; call from an executor. Periods before the account existed come
    back as a near-empty stub PDF rather than a 404, so a statement that parses
    to nothing is treated as absent.
    """
    client.login()

    statements: list[Statement] = []
    today = date.today()
    year, month = today.year, today.month

    for index in range(months):
        period = f"{year:04d}{month:02d}"
        try:
            pdf = client.consolidated(period)
        except DocumentUnavailable:
            pdf = None

        if pdf is not None:
            statement = parse(pdf, period)
            if statement.values:
                statements.append(statement)
            if index + 1 < months:
                time.sleep(FETCH_DELAY)

        month -= 1
        if month == 0:
            year, month = year - 1, 12

    return statements


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the backfill service once per Home Assistant run."""
    if hass.services.has_service(DOMAIN, SERVICE_BACKFILL):
        return

    async def _handle(call: ServiceCall) -> None:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("Get Energy is not configured")

        entry = entries[0]
        coordinator = entry.runtime_data
        months = call.data[ATTR_MONTHS]

        try:
            statements = await hass.async_add_executor_job(
                collect, coordinator.client, months
            )
        except AuthError as err:
            raise HomeAssistantError(f"Get Energy rejected the session: {err}") from err

        if not statements:
            raise HomeAssistantError("no statements found to import")

        # Imported here rather than at module scope: the recorder statistics
        # helper is an internal API, and a future rename should break only the
        # backfill action, not the whole integration.
        from homeassistant.components.recorder.statistics import (
            async_add_external_statistics,
        )

        from .sensor import coefficients  # local import avoids a cycle

        coeffs: Coefficients = coefficients(entry)
        series = build(statements, coeffs, dt_util.DEFAULT_TIME_ZONE)

        for key, rows in series.items():
            async_add_external_statistics(hass, metadata(key), rows)

        _LOGGER.info(
            "imported %d statements as %d statistic series (%d rows)",
            len(statements),
            len(series),
            sum(len(r) for r in series.values()),
        )

    hass.services.async_register(DOMAIN, SERVICE_BACKFILL, _handle, schema=SCHEMA)
