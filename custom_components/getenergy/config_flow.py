"""Config flow for the Get Energy integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from homeassistant.core import callback

from .api import AuthError, GetEnergyClient
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
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


class GetEnergyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collect and verify portal credentials."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> GetEnergyOptionsFlow:
        return GetEnergyOptionsFlow()

    async def _validate(self, data: dict[str, Any]) -> None:
        """Log in once to confirm the credentials work."""
        client = GetEnergyClient(
            username=data[CONF_USERNAME], password=data[CONF_PASSWORD]
        )
        await self.hass.async_add_executor_job(client.login)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()

            try:
                await self._validate(user_input)
            except AuthError:
                # Never loop on this: the portal locks accounts.
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("unexpected error validating Get Energy credentials")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**entry.data, **user_input}
            try:
                await self._validate(data)
            except AuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("unexpected error during Get Energy reauth")
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD, autocomplete="current-password"
                )
            )}),
            errors=errors,
        )


class GetEnergyOptionsFlow(OptionsFlow):
    """Edit the cost structure used to derive prices.

    The defaults were fitted from one Alberta account. Anyone on a different
    tariff, municipality or distributor should run tools/history.py against
    their own statements and set these.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WIRES_PER_KWH,
                    default=current.get(CONF_WIRES_PER_KWH, DEFAULT_WIRES_PER_KWH),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_STANDING_PER_DAY,
                    default=current.get(
                        CONF_STANDING_PER_DAY, DEFAULT_STANDING_PER_DAY
                    ),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_STANDING_PER_MONTH,
                    default=current.get(
                        CONF_STANDING_PER_MONTH, DEFAULT_STANDING_PER_MONTH
                    ),
                ): vol.Coerce(float),
                vol.Required(
                    CONF_GST_RATE,
                    default=current.get(CONF_GST_RATE, DEFAULT_GST_RATE),
                ): vol.Coerce(float),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
