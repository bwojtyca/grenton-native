"""Sidebar panel + websocket API for the live communication monitor."""

from __future__ import annotations

import logging
import os

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "grenton-native"
PANEL_JS_URL = "/grenton_native_frontend/panel.js"
PANEL_ELEMENT = "grenton-native-panel"
_REGISTERED_FLAG = f"{DOMAIN}_panel_registered"


async def async_setup_panel(hass: HomeAssistant) -> None:
    if hass.data.get(_REGISTERED_FLAG):
        return
    hass.data[_REGISTERED_FLAG] = True

    js_path = os.path.join(os.path.dirname(__file__), "panel.js")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_JS_URL, js_path, cache_headers=False)]
    )

    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_check_alive)

    if not frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_register_built_in_panel(
            hass,
            "custom",
            sidebar_title="Grenton Native",
            sidebar_icon="mdi:lan-connect",
            frontend_url_path=PANEL_URL_PATH,
            require_admin=True,
            show_in_sidebar=True,
            config={
                "_panel_custom": {
                    "name": PANEL_ELEMENT,
                    "module_url": PANEL_JS_URL,
                    "embed_iframe": False,
                    "trust_external": False,
                }
            },
        )


def _get_monitor(hass: HomeAssistant):
    monitors = list(hass.data.get(DOMAIN, {}).values())
    return monitors[0] if monitors else None


@websocket_api.websocket_command({vol.Required("type"): "grenton_native/status"})
@websocket_api.require_admin
@callback
def ws_status(hass, connection, msg) -> None:
    monitor = _get_monitor(hass)
    if monitor is None:
        connection.send_error(msg["id"], "not_ready", "Monitor not running")
        return
    connection.send_result(msg["id"], monitor.snapshot())


@websocket_api.websocket_command({vol.Required("type"): "grenton_native/subscribe"})
@websocket_api.require_admin
@callback
def ws_subscribe(hass, connection, msg) -> None:
    """Stream every new wire event to this websocket connection."""
    monitor = _get_monitor(hass)
    if monitor is None:
        connection.send_error(msg["id"], "not_ready", "Monitor not running")
        return

    @callback
    def forward(event: dict) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event))

    monitor.add_subscriber(forward)

    @callback
    def unsubscribe() -> None:
        monitor.remove_subscriber(forward)

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "grenton_native/check_alive",
        vol.Required("serial"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_check_alive(hass, connection, msg) -> None:
    monitor = _get_monitor(hass)
    if monitor is None:
        connection.send_error(msg["id"], "not_ready", "Monitor not running")
        return
    reply = await monitor.check_alive(msg["serial"])
    connection.send_result(msg["id"], {"serial": msg["serial"], "reply": reply})
