"""The Get Energy integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .backfill import async_register
from .const import DOMAIN
from .coordinator import GetEnergyCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Defining async_setup means Home Assistant expects a schema; this integration
# is configured entirely through the UI.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type GetEnergyConfigEntry = ConfigEntry[GetEnergyCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register services that do not depend on a working connection.

    Deliberately not done in async_setup_entry: that awaits the coordinator's
    first refresh, so a transient portal failure would abort setup and leave
    the backfill action unregistered -- which reads to the user as the action
    simply not existing.
    """
    async_register(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: GetEnergyConfigEntry
) -> bool:
    """Set up Get Energy from a config entry."""
    coordinator = GetEnergyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GetEnergyConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
