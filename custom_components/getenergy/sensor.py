"""Sensors for the Get Energy integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import GetEnergyCoordinator

CURRENCY = "CAD"


@dataclass(frozen=True, kw_only=True)
class GetEnergySensorDescription(SensorEntityDescription):
    """Ties a sensor to a key produced by :mod:`.bill`."""

    source_key: str


SENSORS: tuple[GetEnergySensorDescription, ...] = (
    GetEnergySensorDescription(
        key="amount_due",
        source_key="amount_due",
        translation_key="amount_due",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="previous_balance",
        source_key="previous_balance",
        translation_key="previous_balance",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="payments",
        source_key="payments",
        translation_key="payments",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="balance_forward",
        source_key="balance_forward",
        translation_key="balance_forward",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="charges_before_tax",
        source_key="charges_before_tax",
        translation_key="charges_before_tax",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="gst",
        source_key="gst",
        translation_key="gst",
        native_unit_of_measurement=CURRENCY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="electricity_imported",
        source_key="electricity_imported_kwh",
        translation_key="electricity_imported",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="electricity_exported",
        source_key="electricity_exported_kwh",
        translation_key="electricity_exported",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
    ),
    GetEnergySensorDescription(
        key="gas_consumption",
        source_key="gas_consumption_gj",
        translation_key="gas_consumption",
        native_unit_of_measurement="GJ",
        state_class=SensorStateClass.TOTAL,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: GetEnergyCoordinator = entry.runtime_data
    async_add_entities(
        GetEnergySensor(coordinator, entry, description) for description in SENSORS
    )


class GetEnergySensor(CoordinatorEntity[GetEnergyCoordinator], SensorEntity):
    """A single figure read off the latest statement."""

    _attr_has_entity_name = True
    entity_description: GetEnergySensorDescription

    def __init__(
        self,
        coordinator: GetEnergyCoordinator,
        entry: ConfigEntry,
        description: GetEnergySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name="Get Energy",
            configuration_url="https://secure.getenergy.ca/mysecure/portal.html",
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.values.get(self.entity_description.source_key)

    @property
    def available(self) -> bool:
        """Unavailable when the bill carried no matching label.

        Preferred over reporting a guessed number: a label that stops matching
        means the layout changed, and a silently wrong figure would be worse
        than a gap in history.
        """
        return (
            super().available
            and self.entity_description.source_key in self.coordinator.data.values
        )

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        statement = self.coordinator.data.statement
        if statement is None:
            return None
        return {"statement_period": statement.period}
