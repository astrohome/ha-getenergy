"""Update coordinator for the Get Energy integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthError, DocumentUnavailable, GetEnergyClient
from .bill import Statement, parse
from .const import BACKFILL_MONTHS, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class GetEnergyData:
    """What one refresh produced."""

    statement: Statement | None = None
    account_ids: list[str] = field(default_factory=list)

    @property
    def values(self) -> dict[str, Any]:
        return self.statement.values if self.statement else {}


class GetEnergyCoordinator(DataUpdateCoordinator[GetEnergyData]):
    """Fetch the newest statement and parse it.

    Deliberately unhurried: bills are monthly, and the portal locks accounts
    after repeated failed logins, so this never retries credentials itself.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = GetEnergyClient(
            username=entry.data["username"], password=entry.data["password"]
        )

    def _refresh(self) -> GetEnergyData:
        """Blocking work, run in an executor."""
        self.client.login()
        accounts = self.client.account_ids()
        period, pdf = self.client.latest_consolidated(BACKFILL_MONTHS)
        return GetEnergyData(statement=parse(pdf, period), account_ids=accounts)

    async def _async_update_data(self) -> GetEnergyData:
        try:
            return await self.hass.async_add_executor_job(self._refresh)
        except AuthError as err:
            # Surface as a re-auth prompt rather than hammering the login form.
            raise ConfigEntryAuthFailed(str(err)) from err
        except DocumentUnavailable as err:
            raise UpdateFailed(f"statement unavailable: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"error talking to the Get Energy portal: {err}") from err
