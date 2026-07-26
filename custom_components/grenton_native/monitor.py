"""Runtime that owns the native CLU sessions and feeds the monitor panel.

Restartable at runtime: the panel can upload a new ``.omp`` at any time, which
stops the current sessions and starts fresh ones. One
:class:`~.native.client.GrentonCluConnection` per CLU (each with its own report
port). Every wire event is appended to a bounded ring and broadcast to subscribed
websocket clients; a periodic ``checkAlive`` tracks liveness.

Read-only toward Grenton: checkAlive + clientRegister/Report + clientDestroy.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .const import (
    DEFAULT_CHECKALIVE_INTERVAL,
    DEFAULT_INDICES,
    DEFAULT_REPORT_PORT_BASE,
    DOMAIN,
    EVENT_RING_SIZE,
    PROJECT_FILENAME,
)
from .native import events
from .native.cipher import GrentonCipher
from .native.client import GrentonCluConnection, detect_local_ip
from .native.omp import OmpProject, load_omp

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

Subscriber = Callable[[dict[str, Any]], None]


def parse_indices(spec: str) -> list[int]:
    """'0-15' / '0,1,2' / '0-3,7' → list of ints."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


class GrentonRuntime:
    """Single, restartable runtime shared by the panel websocket commands."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._persist_path = hass.config.path(DOMAIN, PROJECT_FILENAME)

        self._ring = events.EventRing(EVENT_RING_SIZE)
        self._subscribers: set[Subscriber] = set()

        self._project: OmpProject | None = None
        self._cipher: GrentonCipher | None = None
        self._connections: dict[str, GrentonCluConnection] = {}
        self._sessions: dict[str, int] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._checkalive_task: asyncio.Task | None = None
        self._active = False

        # defaults (could later be made configurable from the panel)
        self._report_port_base = DEFAULT_REPORT_PORT_BASE
        self._checkalive_interval = DEFAULT_CHECKALIVE_INTERVAL
        # Quiet by default: only checkAlive on start. The CLU pushes a
        # clientReport on every change of a subscribed feature, and its own
        # clock/uptime change every second — so auto-subscribing floods the log.
        # Observe deliberately via the panel's "Watch object" control instead.
        self._subscribe = False
        self._indices = parse_indices(DEFAULT_INDICES)

    @property
    def configured(self) -> bool:
        return self._project is not None

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def async_load_persisted(self) -> None:
        """Auto-start from a previously uploaded project, if present."""
        path = Path(self._persist_path)
        if not path.exists():
            return
        try:
            project = await self.hass.async_add_executor_job(load_omp, str(path))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not load persisted project: %s", err)
            return
        await self._start(project)

    async def async_upload(self, omp_bytes: bytes) -> OmpProject:
        """Persist an uploaded .omp, then (re)start sessions from it."""

        def _save_and_load() -> OmpProject:
            path = Path(self._persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(omp_bytes)
            return load_omp(str(path))

        project = await self.hass.async_add_executor_job(_save_and_load)
        await self.async_stop()
        await self._start(project)
        return project

    async def _start(self, project: OmpProject) -> None:
        self._project = project
        self._cipher = GrentonCipher(project.cipher_key, project.cipher_iv)
        self._status = {}
        clus = [c for c in project.clus if c.ip]
        for index, clu in enumerate(clus):
            report_port = self._report_port_base + index
            self._status[clu.serial] = {
                "serial": clu.serial,
                "ip": clu.ip,
                "object_name": clu.object_name,
                "report_port": report_port,
                "alive": None,
                "reply": None,
                "last_seen": None,
            }
            try:
                host_ip = detect_local_ip(clu.ip)
                conn = GrentonCluConnection(
                    clu_ip=clu.ip,
                    cipher=self._cipher,
                    host_ip=host_ip,
                    report_port=report_port,
                )
                conn.on_event = self._make_hook(clu.serial)
                await conn.open()
                self._connections[clu.serial] = conn
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("[%s] failed to open connection: %s", clu.serial, err)
                continue
            await self._do_check_alive(clu.serial)
            if self._subscribe and self._indices:
                await self._subscribe_clu(clu)
        self._checkalive_task = self.hass.loop.create_task(self._checkalive_loop())
        self._active = True

    async def async_start(self) -> None:
        """(Re)start monitoring from the current or previously-uploaded project."""
        if self._active:
            return
        if self._project is not None:
            await self._start(self._project)
        else:
            await self.async_load_persisted()

    async def async_stop(self) -> None:
        self._active = False
        if self._checkalive_task is not None:
            self._checkalive_task.cancel()
            self._checkalive_task = None
        for serial, conn in self._connections.items():
            session = self._sessions.get(serial)
            try:
                if session is not None:
                    await conn.client_destroy(session)
            except Exception:  # noqa: BLE001
                pass
            conn.close()
        self._connections.clear()
        self._sessions.clear()

    # ── event plumbing ────────────────────────────────────────────────────

    def _make_hook(self, serial: str):
        def hook(direction: str, kind: str, msg_id: str | None, summary: str, detail: Any) -> None:
            event = self._ring.add(
                ts=time.time(),
                clu=serial,
                direction=direction,
                kind=kind,
                msg_id=msg_id,
                summary=summary,
                detail=detail,
            )
            payload = event.as_dict()
            for cb in list(self._subscribers):
                try:
                    cb(payload)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("monitor subscriber raised")

        return hook

    def add_subscriber(self, cb: Subscriber) -> None:
        self._subscribers.add(cb)

    def remove_subscriber(self, cb: Subscriber) -> None:
        self._subscribers.discard(cb)

    def snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "active": self._active,
            "clus": list(self._status.values()),
            "events": self._ring.snapshot_dicts(),
            "last_seq": self._ring.last_seq,
        }

    # ── actions ───────────────────────────────────────────────────────────

    async def check_alive(self, serial: str) -> str | None:
        return await self._do_check_alive(serial)

    async def watch(self, serial: str, object_name: str, indices: str) -> None:
        """Re-subscribe a CLU to a specific object's feature indices.

        For the "change a device and watch the log" test: point at e.g. a light's
        object with index 0 (Value), then toggle it — the clientReport pushes
        will carry the new value. Replaces this CLU's current subscription.
        """
        conn = self._connections.get(serial)
        if conn is None:
            raise ValueError(f"CLU {serial} is not connected")
        old = self._sessions.get(serial)
        if old is not None:
            try:
                await conn.client_destroy(old)
            except Exception:  # noqa: BLE001
                pass
        session = secrets.randbelow(60000) + 1
        self._sessions[serial] = session
        keys = [(object_name, i) for i in parse_indices(indices)]
        await conn.client_register(keys, session, lambda *_: None)

    async def _do_check_alive(self, serial: str) -> str | None:
        conn = self._connections.get(serial)
        if conn is None:
            return None
        reply = await conn.check_alive()
        status = self._status.setdefault(serial, {"serial": serial})
        status["alive"] = reply is not None
        status["reply"] = reply
        if reply is not None:
            status["last_seen"] = time.time()
        self._ring.add(
            ts=time.time(),
            clu=serial,
            direction=events.DIRECTION_IN,
            kind=events.KIND_STATUS,
            summary=f"alive={status['alive']} ({reply})",
        )
        return reply

    async def _subscribe_clu(self, clu) -> None:
        conn = self._connections.get(clu.serial)
        if conn is None:
            return
        session = secrets.randbelow(60000) + 1
        self._sessions[clu.serial] = session
        keys = [(clu.object_name, i) for i in self._indices]
        try:
            await conn.client_register(keys, session, lambda *_: None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[%s] clientRegister failed: %s", clu.serial, err)

    async def _checkalive_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._checkalive_interval)
                for serial in list(self._connections):
                    await self._do_check_alive(serial)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                _LOGGER.exception("checkalive loop error")
