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
import json
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
    STATE_FILENAME,
)
from .native import events, mapping
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
        self._state_path = hass.config.path(DOMAIN, STATE_FILENAME)

        self._ring = events.EventRing(EVENT_RING_SIZE)
        self._subscribers: set[Subscriber] = set()

        self._project: OmpProject | None = None
        self._cipher: GrentonCipher | None = None
        self._connections: dict[str, GrentonCluConnection] = {}
        # serial → set of active subscription sessions (one per single-object
        # watch, or several — chunked — for the "observe visible" identify mode).
        self._sessions: dict[str, set[int]] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._checkalive_task: asyncio.Task | None = None
        self._active = False           # sessions actually open?
        self._desired_active = True    # user intent (the kill-switch), persisted

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

    async def async_start_runtime(self) -> None:
        """Setup entry point: restore the kill-switch state, load a previously
        uploaded project (so the object map works even while disconnected), and
        open sessions only if the user last left us connected."""
        self._desired_active = await self.hass.async_add_executor_job(self._read_desired)
        path = Path(self._persist_path)
        if path.exists():
            try:
                self._project = await self.hass.async_add_executor_job(load_omp, str(path))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not load persisted project: %s", err)
        if self._project is not None and self._desired_active:
            await self._start(self._project)

    async def async_upload(self, omp_bytes: bytes) -> OmpProject:
        """Persist an uploaded .omp and load it. Sessions (re)open only if the
        kill-switch is currently 'connected'."""

        def _save_and_load() -> OmpProject:
            path = Path(self._persist_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(omp_bytes)
            return load_omp(str(path))

        project = await self.hass.async_add_executor_job(_save_and_load)
        self._project = project
        await self.async_stop()
        if self._desired_active:
            await self._start(project)
        return project

    # ── kill-switch (persisted connect/disconnect) ─────────────────────────

    async def set_active(self, active: bool) -> None:
        """Connect (open sessions) or fully disconnect (close everything).
        Persisted, so a manual disconnect survives a Home Assistant restart."""
        self._desired_active = active
        await self.hass.async_add_executor_job(self._write_desired, active)
        if active:
            if self._project is not None and not self._active:
                await self._start(self._project)
        else:
            await self.async_stop()

    def _read_desired(self) -> bool:
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                return bool(json.load(fh).get("active", True))
        except (OSError, ValueError):
            return True

    def _write_desired(self, active: bool) -> None:
        try:
            path = Path(self._state_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"active": active}), encoding="utf-8")
        except OSError as err:  # noqa: BLE001
            _LOGGER.warning("Could not persist runtime state: %s", err)

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

    async def async_stop(self) -> None:
        self._active = False
        if self._checkalive_task is not None:
            self._checkalive_task.cancel()
            self._checkalive_task = None
        await self._destroy_sessions()  # clientDestroy every session while sockets live
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()

    async def _destroy_sessions(self, serials: list[str] | None = None) -> None:
        """Send clientDestroy for tracked sessions (all CLUs, or the given ones)."""
        targets = serials if serials is not None else list(self._sessions.keys())
        for serial in targets:
            conn = self._connections.get(serial)
            for session in self._sessions.pop(serial, set()):
                if conn is None:
                    continue
                try:
                    await conn.client_destroy(session)
                except Exception:  # noqa: BLE001
                    pass

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

    def objects_map(self) -> list[dict[str, Any]]:
        """The .omp object inventory with built-in features/events + a proposed
        HA domain — the data behind the panel's object map. Heavy, so served on
        demand (not in the frequently-polled status snapshot)."""
        if self._project is None:
            return []
        out: list[dict[str, Any]] = []
        for obj in self._project.objects:
            proposal = mapping.propose(obj.grenton_id, obj.type)
            out.append(
                {
                    "grenton_id": obj.grenton_id,
                    "obj_id": obj.obj_id,
                    "clu": obj.clu,
                    "name": obj.name,
                    "type": obj.type,
                    "domain": proposal["domain"],
                    "index": proposal["index"],
                    "note": proposal["note"],
                    "features": [
                        {
                            "name": f.name,
                            "index": f.index,
                            "param_type": f.param_type,
                            "unit": f.unit,
                            "access": f.access,
                            "constraint": f.constraint,
                            "init": f.init_value,
                            "visible": f.visible,
                        }
                        for f in obj.features
                    ],
                    "events": [e.name for e in obj.events],
                }
            )
        return out

    # ── actions ───────────────────────────────────────────────────────────

    async def check_alive(self, serial: str) -> str | None:
        return await self._do_check_alive(serial)

    async def watch(self, serial: str, object_name: str, indices: str) -> int:
        """Re-subscribe a CLU to a specific object's feature indices.

        Returns the subscription ``session`` so the caller can correlate the
        ``clientReport`` pushes (whose payload is ``clientReport:<session>:{...}``)
        back to the requested indices. Replaces this CLU's current subscription.
        """
        conn = self._connections.get(serial)
        if conn is None:
            raise ValueError(f"CLU {serial} is not connected")
        await self._destroy_sessions([serial])
        session = secrets.randbelow(60000) + 1
        self._sessions[serial] = {session}
        keys = [(object_name, i) for i in parse_indices(indices)]
        await conn.client_register(keys, session, lambda *_: None)
        return session

    async def watch_visible(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Identify mode: subscribe to many objects at once (e.g. all filtered
        DIN inputs), so physically pressing a button reveals which object fired.

        ``targets`` is a list of ``{clu, obj, index}``. Keys are grouped per CLU
        and chunked (the CLU caps a single clientRegister payload), each chunk its
        own session. Returns ``[{session, keys:[{clu,obj,index}]}]`` so the panel
        can map each report's positional values back to the right object rows.
        """
        await self._destroy_sessions()  # fresh identify run

        by_clu: dict[str, list[tuple[str, int]]] = {}
        for target in targets:
            try:
                index = int(target.get("index", 0))
            except (TypeError, ValueError):
                index = 0
            by_clu.setdefault(target["clu"], []).append((target["obj"], index))

        chunk_size = 25  # stay well under the ~2000-byte clientRegister payload cap
        result: list[dict[str, Any]] = []
        for clu, keys in by_clu.items():
            conn = self._connections.get(clu)
            if conn is None:
                continue
            for start in range(0, len(keys), chunk_size):
                chunk = keys[start : start + chunk_size]
                session = secrets.randbelow(60000) + 1
                self._sessions.setdefault(clu, set()).add(session)
                try:
                    await conn.client_register(chunk, session, lambda *_: None)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("[%s] identify register failed: %s", clu, err)
                    continue
                result.append(
                    {
                        "session": session,
                        "keys": [{"clu": clu, "obj": o, "index": i} for (o, i) in chunk],
                    }
                )
        return result

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
        self._sessions.setdefault(clu.serial, set()).add(session)
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
