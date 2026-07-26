"""Grenton Native — a spike integration that talks to CLUs over the native
encrypted UDP protocol and exposes a live communication-monitor panel.

The integration itself is configuration-free: adding it just registers the
panel. Everything else — uploading the Object Manager ``.omp`` and watching the
live communication — happens inside the panel.

Import-time is kept Home-Assistant-free (all HA imports are lazy, inside the
setup functions) so the ``native`` protocol package can also be used standalone
by the spike script without Home Assistant installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

RUNTIME_KEY = "runtime"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .monitor import GrentonRuntime
    from .panel import async_setup_panel

    runtime = GrentonRuntime(hass)
    hass.data.setdefault(DOMAIN, {})[RUNTIME_KEY] = runtime

    await async_setup_panel(hass)
    # Auto-start if a project was uploaded in a previous session.
    await runtime.async_load_persisted()

    _LOGGER.info("Grenton Native panel ready (configured=%s)", runtime.configured)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = hass.data.get(DOMAIN, {}).pop(RUNTIME_KEY, None)
    if runtime is not None:
        await runtime.async_stop()
    return True
