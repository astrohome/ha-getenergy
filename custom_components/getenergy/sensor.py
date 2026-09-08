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

from .const import (
    CONF_GST_RATE,
    CONF_STANDING_PER_DAY,
    CONF_STANDING_PER_MONTH,
    CONF_WIRES_PER_KWH,
    DEFAULT_GST_RATE,
    DEFAULT_STANDING_PER_DAY,
    DEFAULT_STANDING_PER_MONTH,
    DEFAULT_WIRES_PER_KWH,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import GetEnergyCoordinator
from .pricing import Coefficients, compute

CURRENCY = "CAD"
PRICE_UNIT = "CAD/kWh"


def coefficients(entry: ConfigEntry) -> Coefficients:
    """Read the cost structure from options, falling back to the defaults."""
    opts = entry.options
    return Coefficients(
        wires_per_kwh=opts.get(CONF_WIRES_PER_KWH, DEFAULT_WIRES_PER_KWH),
        standing_per_day=opts.get(CONF_STANDING_PER_DAY, DEFAULT_STANDING_PER_DAY),
        standing_per_month=opts.get(
            CONF_STANDING_PER_MONTH, DEFAULT_STANDING_PER_MONTH
        ),
        gst_rate=opts.get(CONF_GST_RATE, DEFAULT_GST_RATE),
    )


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
    entities: list[SensorEntity] = [
        GetEnergySensor(coordinator, entry, description) for description in SENSORS
    ]
    entities += [
        GetEnergyPriceSensor(coordinator, entry, key) for key in PRICE_KEYS
    ]
    async_add_entities(entities)


#: Attach these to the energy dashboard as "use an entity with current price".
PRICE_KEYS = ("import_price", "export_price", "energy_rate", "standing_charge")


class GetEnergyPriceSensor(CoordinatorEntity[GetEnergyCoordinator], SensorEntity):
    """A price derived from the statement plus the configured cost structure."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: GetEnergyCoordinator, entry: ConfigEntry, key: str
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._entry = entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = (
            CURRENCY if key == "standing_charge" else PRICE_UNIT
        )
        self._attr_suggested_display_precision = 2 if key == "standing_charge" else 4
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name="Get Energy",
            configuration_url="https://secure.getenergy.ca/mysecure/portal.html",
        )

    @property
    def _prices(self):
        return compute(
            self.coordinator.data.statement, coefficients(self._entry)
        )

    @property
    def native_value(self) -> float | None:
        prices = self._prices
        if self._key == "standing_charge":
            days = 30
            statement = self.coordinator.data.statement
            if statement is not None:
                days = statement.meta.get("service_days") or 30
            return round(
                prices.standing_per_day * days + prices.standing_per_month, 2
            )
        return getattr(prices, self._key)

    @property
    def available(self) -> bool:
        """Standing charges come from configuration, so they are always known.

        The rate-derived prices are unavailable when the statement carries no
        rate: a stale or guessed price would be multiplied by every kWh.
        """
        if not super().available:
            return False
        if self._key == "standing_charge":
            return True
        return self._prices.available

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self._key != "import_price":
            return None
        coeffs = coefficients(self._entry)
        return {
            "energy_rate": self._prices.energy_rate,
            "wires_per_kwh": coeffs.wires_per_kwh,
            "gst_rate": coeffs.gst_rate,
            "excludes": "standing charges - see the standing charge sensor",
        }


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
