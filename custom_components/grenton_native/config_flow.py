"""Config flow: a one-click, single-instance add.

The integration is configuration-free — adding it just registers the panel.
Uploading the Object Manager ``.omp`` and everything else happens in the panel.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class GrentonNativeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Grenton Native", data={})
        # Empty confirm form: just a Submit button.
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))
