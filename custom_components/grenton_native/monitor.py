"""Runtime that keeps native sessions to the CLUs and feeds the monitor panel.

One :class:`~.native.client.GrentonCluConnection` per CLU (each with its own
report port so multiple CLUs don't fight over one UDP port). Every wire event is
appended to a bounded ring and broadcast to any subscribed websocket clients.
A periodic ``checkAlive`` tracks liveness.

Read-only toward Grenton: checkAlive + clientRegister/Report + clientDestroy.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .const import EVENT_RING_SIZE
from .native import events
from .native.cipher import GrentonCipher
from .native.client import GrentonCluConnection, detect_local_ip

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .native.omp import OmpProject

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


class GrentonMonitor:
    def __init__(
        self,
        hass: HomeAssistant,
        project: OmpProject,
        *,
        report_port_base: int,
        checkalive_interval: int,
        subscribe: bool,
        indices: str,
    ) -> None:
        self.hass = hass
        self._project = project
        self._report_port_base = report_port_base
        self._checkalive_interval = checkalive_interval
        self._subscribe = subscribe
        self._indices = parse_indices(indices)
        self._cipher = GrentonCipher(project.cipher_key, project.cipher_iv)

        self._ring = events.EventRing(EVENT_RING_SIZE)
        self._subscribers: set[Subscriber] = set()
        self._connections: dict[str, GrentonCluConnection] = {}
        self._sessions: dict[str, int] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._checkalive_task: asyncio.Task | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        clus = [c for c in self._project.clus if c.ip]
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

    async def async_stop(self) -> None:
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
            "clus": list(self._status.values()),
            "events": self._ring.snapshot_dicts(),
            "last_seq": self._ring.last_seq,
        }

    # ── actions ───────────────────────────────────────────────────────────

    async def check_alive(self, serial: str) -> str | None:
        return await self._do_check_alive(serial)

    async def _do_check_alive(self, serial: str) -> str | None:
        conn = self._connections.get(serial)
        if conn is None:
            return None
        reply = await conn.check_alive()
        status = self._status.setdefault(serial, {"serial": serial})
        status["alive"] = reply is not None
        status["reply"] = reply
        status["last_seen"] = time.time() if reply is not None else status.get("last_seen")
        # surface liveness as a status event too
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
