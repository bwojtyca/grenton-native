"""Sidebar panel + websocket API for the live communication monitor.

The panel is where everything happens: upload the ``.omp`` (drag & drop), see
each CLU's liveness, and follow the live native communication.
"""

from __future__ import annotations

import base64
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
RUNTIME_KEY = "runtime"


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
    websocket_api.async_register_command(hass, ws_upload_omp)
    websocket_api.async_register_command(hass, ws_watch)
    websocket_api.async_register_command(hass, ws_set_active)
    websocket_api.async_register_command(hass, ws_objects)

    # Cache-bust the ES module URL with the file's mtime — HA/browsers cache the
    # panel module aggressively by URL, so a fixed URL keeps serving a stale
    # panel.js even after a hard refresh. A changing ?v= forces a fresh fetch.
    try:
        mtime = int(os.path.getmtime(js_path))
    except OSError:
        mtime = 0
    module_url = f"{PANEL_JS_URL}?v={mtime}"

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
                    "module_url": module_url,
                    "embed_iframe": False,
                    "trust_external": False,
                }
            },
        )


def _get_runtime(hass: HomeAssistant):
    return hass.data.get(DOMAIN, {}).get(RUNTIME_KEY)


@websocket_api.websocket_command({vol.Required("type"): "grenton_native/status"})
@websocket_api.require_admin
@callback
def ws_status(hass, connection, msg) -> None:
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    connection.send_result(msg["id"], runtime.snapshot())


@websocket_api.websocket_command({vol.Required("type"): "grenton_native/subscribe"})
@websocket_api.require_admin
@callback
def ws_subscribe(hass, connection, msg) -> None:
    """Stream every new wire event to this websocket connection."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return

    @callback
    def forward(event: dict) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], event))

    runtime.add_subscriber(forward)

    @callback
    def unsubscribe() -> None:
        runtime.remove_subscriber(forward)

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
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    reply = await runtime.check_alive(msg["serial"])
    connection.send_result(msg["id"], {"serial": msg["serial"], "reply": reply})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "grenton_native/watch",
        vol.Required("serial"): str,
        vol.Required("object"): str,
        vol.Required("indices"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_watch(hass, connection, msg) -> None:
    """Re-subscribe a CLU to a chosen object's feature indices."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    try:
        await runtime.watch(msg["serial"], msg["object"], msg["indices"])
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "watch_failed", str(err))
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "grenton_native/set_active",
        vol.Required("active"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_active(hass, connection, msg) -> None:
    """Stop or (re)start monitoring without removing the integration."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    if msg["active"]:
        await runtime.async_start()
    else:
        await runtime.async_stop()
    connection.send_result(msg["id"], runtime.snapshot())


@websocket_api.websocket_command({vol.Required("type"): "grenton_native/objects"})
@websocket_api.require_admin
@callback
def ws_objects(hass, connection, msg) -> None:
    """The .omp object map (objects + built-in features + events)."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    connection.send_result(msg["id"], {"objects": runtime.objects_map()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "grenton_native/upload_omp",
        vol.Required("omp_base64"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_upload_omp(hass, connection, msg) -> None:
    """Receive an uploaded .omp (base64), persist it and (re)start the runtime."""
    runtime = _get_runtime(hass)
    if runtime is None:
        connection.send_error(msg["id"], "not_ready", "Runtime not initialised")
        return
    try:
        data = base64.b64decode(msg["omp_base64"])
        await runtime.async_upload(data)
    except Exception as err:  # noqa: BLE001 - surface any parse/copy failure
        _LOGGER.warning("upload_omp failed: %s", err)
        connection.send_error(msg["id"], "invalid_omp", str(err))
        return
    connection.send_result(msg["id"], runtime.snapshot())
