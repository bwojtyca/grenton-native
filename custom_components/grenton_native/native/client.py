"""Asyncio UDP client for one CLU.

Two sockets:

* **command** — ephemeral local port, "connected" to ``<clu_ip>:1234``. Requests
  go out here; direct replies (checkAlive, the clientRegister ack) come back here.
* **report** — bound to ``0.0.0.0:<report_port>`` (default 4344). The CLU delivers
  ongoing ``clientReport`` pushes here (it is told the address in clientRegister).

Both feed one decrypt+parse path. A response whose ``msg_id`` matches a pending
request resolves that request; any ``clientReport`` is also delivered to the
report callback (so the register ack's initial values are surfaced too).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from . import events, protocol
from .cipher import GrentonCipher
from .protocol import Response

_LOGGER = logging.getLogger(__name__)

COMMAND_PORT = 1234
DEFAULT_REPORT_PORT = 4344

ReportCallback = Callable[[int, list[protocol.LuaValue], Response], None]
# (direction, kind, msg_id, summary, detail)
EventHook = Callable[[str, str, "str | None", str, object], None]


def detect_local_ip(target_ip: str, target_port: int = COMMAND_PORT) -> str:
    """Local IP the OS would use to reach ``target_ip`` (no packets sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_ip, target_port))
        return sock.getsockname()[0]
    finally:
        sock.close()


class _UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_datagram: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._on_datagram = on_datagram
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)


class GrentonCluConnection:
    """A single CLU's encrypted UDP session (no login — the key is the auth)."""

    def __init__(
        self,
        clu_ip: str,
        cipher: GrentonCipher,
        host_ip: str,
        report_port: int = DEFAULT_REPORT_PORT,
        command_port: int = COMMAND_PORT,
        terminator: str = protocol.REQUEST_TERMINATOR,
    ) -> None:
        self.clu_ip = clu_ip
        self.host_ip = host_ip
        self.report_port = report_port
        self.command_port = command_port
        self._cipher = cipher
        self._terminator = terminator
        self._pending: dict[str, asyncio.Future[Response]] = {}
        self._report_cb: ReportCallback | None = None
        self._cmd_transport: asyncio.DatagramTransport | None = None
        self._report_transport: asyncio.DatagramTransport | None = None
        # Optional observer for every wire event (used by the live monitor).
        self.on_event: EventHook | None = None

    def _emit(
        self,
        direction: str,
        kind: str,
        msg_id: str | None,
        summary: str,
        detail: object = None,
    ) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(direction, kind, msg_id, summary, detail)
        except Exception:  # noqa: BLE001 - an observer must never break I/O
            _LOGGER.exception("on_event hook raised")

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        self._cmd_transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self._on_datagram),
            remote_addr=(self.clu_ip, self.command_port),
        )
        self._report_transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self._on_datagram),
            local_addr=("0.0.0.0", self.report_port),
        )
        _LOGGER.debug(
            "opened: cmd→%s:%d, reports←0.0.0.0:%d (host_ip=%s)",
            self.clu_ip, self.command_port, self.report_port, self.host_ip,
        )

    def set_report_callback(self, cb: ReportCallback | None) -> None:
        self._report_cb = cb

    def close(self) -> None:
        for transport in (self._cmd_transport, self._report_transport):
            if transport is not None:
                transport.close()
        self._cmd_transport = self._report_transport = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    # ── requests ────────────────────────────────────────────────────────────

    async def request(self, payload: str, timeout: float = 5.0) -> Response | None:
        if self._cmd_transport is None:
            raise RuntimeError("connection not open")
        msg_id, raw = protocol.build_request(self.host_ip, payload, terminator=self._terminator)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Response] = loop.create_future()
        self._pending[msg_id] = fut
        _LOGGER.debug("→ %s", raw.strip())
        self._emit(events.DIRECTION_OUT, events.KIND_REQUEST, msg_id, payload)
        self._cmd_transport.sendto(self._cipher.encrypt(raw.encode()))
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            _LOGGER.warning("request timed out (msg_id=%s): %s", msg_id, payload)
            self._emit(events.DIRECTION_IN, events.KIND_ERROR, msg_id, f"timeout: {payload}")
            return None
        finally:
            self._pending.pop(msg_id, None)

    async def check_alive(self, timeout: float = 5.0) -> str | None:
        """Return the CLU's reply payload (serial / ``true`` / ``emergency``)."""
        resp = await self.request(protocol.check_alive_payload(), timeout=timeout)
        return resp.payload if resp else None

    async def client_register(
        self,
        keys: list[tuple[str, int]],
        session: int,
        report_cb: ReportCallback,
        timeout: float = 5.0,
    ) -> tuple[int, list[protocol.LuaValue]] | None:
        """Subscribe; returns the initial ``(session, values)`` from the ack."""
        self._report_cb = report_cb
        payload = protocol.client_register_payload(
            self.host_ip, self.report_port, session, keys
        )
        resp = await self.request(payload, timeout=timeout)
        if resp and resp.is_client_report:
            return protocol.parse_client_report(resp.payload)
        return None

    async def client_destroy(self, session: int, timeout: float = 3.0) -> None:
        await self.request(
            protocol.client_destroy_payload(self.host_ip, self.report_port, session),
            timeout=timeout,
        )

    # ── receive path ─────────────────────────────────────────────────────────

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        plaintext = self._cipher.decrypt(data)
        if plaintext is None:
            _LOGGER.warning("undecryptable datagram from %s (%d bytes)", addr, len(data))
            self._emit(
                events.DIRECTION_IN, events.KIND_ERROR, None,
                f"undecryptable {len(data)} bytes from {addr[0]}",
            )
            return
        text = plaintext.decode("utf-8", "replace").strip()
        resp = protocol.parse_response(text)
        if resp is None:
            _LOGGER.debug("unrecognised message from %s: %r", addr, text)
            return
        _LOGGER.debug("← %s", text)

        fut = self._pending.get(resp.msg_id)
        if fut is not None and not fut.done():
            fut.set_result(resp)

        if resp.is_client_report:
            parsed = protocol.parse_client_report(resp.payload)
            values = parsed[1] if parsed else None
            self._emit(events.DIRECTION_IN, events.KIND_REPORT, resp.msg_id, resp.payload, values)
            if parsed is not None and self._report_cb is not None:
                session, report_values = parsed
                try:
                    self._report_cb(session, report_values, resp)
                except Exception:  # noqa: BLE001 - never let a callback kill the loop
                    _LOGGER.exception("report callback raised")
        else:
            self._emit(events.DIRECTION_IN, events.KIND_RESPONSE, resp.msg_id, resp.payload)
