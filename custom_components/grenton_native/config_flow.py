"""Config flow: point the integration at an Object Manager ``.omp`` on the host.

The `.omp` carries the project AES key + CLU topology. We validate by loading it;
the path (not the key) is what gets stored in the config entry, and the file is
re-read on every setup so the secret is never duplicated into HA storage.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_CHECKALIVE_INTERVAL,
    CONF_INDICES,
    CONF_OMP_PATH,
    CONF_REPORT_PORT_BASE,
    CONF_SUBSCRIBE,
    DEFAULT_CHECKALIVE_INTERVAL,
    DEFAULT_INDICES,
    DEFAULT_REPORT_PORT_BASE,
    DOMAIN,
)
from .native.omp import load_omp


class GrentonNativeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            path = user_input[CONF_OMP_PATH]
            try:
                project = await self.hass.async_add_executor_job(load_omp, path)
            except FileNotFoundError:
                errors["base"] = "not_found"
            except Exception:  # noqa: BLE001 - any parse/zip failure
                errors["base"] = "invalid_omp"
            else:
                await self.async_set_unique_id(f"{DOMAIN}:{path}")
                self._abort_if_unique_id_configured()
                title = project.clus[0].serial if project.clus else "project"
                return self.async_create_entry(
                    title=f"Grenton Native ({title})", data=user_input
                )

        default_path = self.hass.config.path(DOMAIN, "project.omp")
        schema = vol.Schema(
            {
                vol.Required(CONF_OMP_PATH, default=default_path): str,
                vol.Optional(
                    CONF_REPORT_PORT_BASE, default=DEFAULT_REPORT_PORT_BASE
                ): int,
                vol.Optional(
                    CONF_CHECKALIVE_INTERVAL, default=DEFAULT_CHECKALIVE_INTERVAL
                ): int,
                vol.Optional(CONF_SUBSCRIBE, default=True): bool,
                vol.Optional(CONF_INDICES, default=DEFAULT_INDICES): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
