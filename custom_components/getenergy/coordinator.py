"""Update coordinator for the Get Energy integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AuthError,
    DocumentUnavailable,
    GetEnergyClient,
    StatementNotFound,
)
from .bill import Statement, parse
from .const import DOMAIN, ROUTINE_LOOKBACK_MONTHS
from .schedule import UNKNOWN_INTERVAL, next_poll, statement_date

_LOGGER = logging.getLogger(__name__)


@dataclass
class GetEnergyData:
    """What one refresh produced."""

    statement: Statement | None = None

    @property
    def values(self) -> dict[str, Any]:
        return self.statement.values if self.statement else {}


class GetEnergyCoordinator(DataUpdateCoordinator[GetEnergyData]):
    """Fetch the newest statement, and predict when to look for the next one.

    Bills are monthly, so a fixed short interval would mean hundreds of
    pointless requests a month against someone else's billing system. After
    each successful fetch the interval is set from the statement's own date:
    quiet until a new bill is plausible, then daily until it appears.

    The portal also locks accounts after repeated failed logins, so credentials
    are never retried automatically -- an auth failure raises a re-auth prompt.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UNKNOWN_INTERVAL,
            config_entry=entry,
        )
        self.client = GetEnergyClient(
            username=entry.data["username"], password=entry.data["password"]
        )

    def _refresh(self) -> GetEnergyData:
        """Blocking work, run in an executor.

        Deliberately makes no GWT-RPC call. Account discovery used to run here
        although nothing read its result, and it is the one request that pins
        the vendor's build hashes: after a portal redeploy it 403'd, which
        invalidated the session and was reported as an auth failure, so every
        successful re-auth was followed by another re-auth prompt. The
        consolidated statement needs only the session and its CSRF token.
        """
        self.client.login()
        period, pdf = self.client.latest_consolidated(ROUTINE_LOOKBACK_MONTHS)
        return GetEnergyData(statement=parse(pdf, period))

    def _reschedule(self, statement: Statement | None) -> None:
        """Set the next interval from the statement's own date."""
        last = statement_date(statement)
        self.update_interval = next_poll(last, date.today())
        _LOGGER.debug(
            "next check in %s (last statement %s)", self.update_interval, last
        )

    async def _async_update_data(self) -> GetEnergyData:
        try:
            data = await self.hass.async_add_executor_job(self._refresh)
        except AuthError as err:
            # Surface as a re-auth prompt rather than hammering the login form.
            raise ConfigEntryAuthFailed(str(err)) from err
        except StatementNotFound as err:
            # No bill yet in the lookback window. Keep the previous data and
            # check again on the normal cadence rather than treating it as a
            # failure, which would make the coordinator retry aggressively.
            _LOGGER.debug("no statement available yet: %s", err)
            previous = self.data or GetEnergyData()
            self._reschedule(previous.statement)
            return previous
        except DocumentUnavailable as err:
            # Transient: the document service is briefly unwell. Says nothing
            # about whether a bill exists.
            raise UpdateFailed(f"document service unavailable: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"error talking to the Get Energy portal: {err}") from err

        self._reschedule(data.statement)
        return data
