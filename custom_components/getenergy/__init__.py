"""The Get Energy integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .backfill import async_register
from .coordinator import GetEnergyCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type GetEnergyConfigEntry = ConfigEntry[GetEnergyCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: GetEnergyConfigEntry
) -> bool:
    """Set up Get Energy from a config entry."""
    coordinator = GetEnergyCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # A backfill re-downloads every past bill, so it is on demand only.
    async_register(hass)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: GetEnergyConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
