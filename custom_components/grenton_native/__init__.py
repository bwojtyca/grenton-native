"""Grenton Native — a spike integration that talks to CLUs over the native
encrypted UDP protocol (keyed from the Object Manager ``.omp``) and exposes a
live communication-monitor panel.

Import-time is kept Home-Assistant-free (all HA imports are lazy, inside the
setup functions) so the ``native`` protocol package can also be used standalone
by the spike script without Home Assistant installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .monitor import GrentonMonitor
    from .native.omp import load_omp
    from .panel import async_setup_panel

    path = entry.data[CONF_OMP_PATH]
    try:
        project = await hass.async_add_executor_job(load_omp, path)
    except Exception as err:  # noqa: BLE001 - surface as a setup failure
        _LOGGER.error("Failed to load .omp %s: %s", path, err)
        return False

    monitor = GrentonMonitor(
        hass,
        project,
        report_port_base=entry.data.get(CONF_REPORT_PORT_BASE, DEFAULT_REPORT_PORT_BASE),
        checkalive_interval=entry.data.get(
            CONF_CHECKALIVE_INTERVAL, DEFAULT_CHECKALIVE_INTERVAL
        ),
        subscribe=entry.data.get(CONF_SUBSCRIBE, True),
        indices=entry.data.get(CONF_INDICES, DEFAULT_INDICES),
    )
    await monitor.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = monitor
    await async_setup_panel(hass)

    _LOGGER.info(
        "Grenton Native started: %d CLU(s) from %s", len(project.clus), path
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    monitor = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if monitor is not None:
        await monitor.async_stop()
    return True
