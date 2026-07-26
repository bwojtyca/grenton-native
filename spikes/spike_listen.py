#!/usr/bin/env python3
"""Spike: prove we can talk to a real CLU natively and receive live push.

Run against your own network. Read-only toward Grenton (checkAlive + subscribe +
destroy — no device actions). The project AES key is loaded from the .omp and is
never printed.

    python spikes/spike_listen.py --omp /path/to/project.omp -v

Steps: load .omp → checkAlive one CLU → subscribe to that CLU's own system
features → print live clientReport pushes for a while → clientDestroy → exit.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
from pathlib import Path

# Allow running straight from the repo without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grenton_native.cipher import GrentonCipher  # noqa: E402
from grenton_native.client import (  # noqa: E402
    DEFAULT_REPORT_PORT,
    GrentonCluConnection,
    detect_local_ip,
)
from grenton_native.omp import load_omp  # noqa: E402
from grenton_native.protocol import Response  # noqa: E402

_LOGGER = logging.getLogger("spike")


def parse_indices(spec: str) -> list[int]:
    """Accept '0-15' or '0,1,2' or a mix like '0-3,7,10-12'."""
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


async def run(args: argparse.Namespace) -> int:
    project = load_omp(args.omp)
    _LOGGER.info(
        "loaded .omp: cipher key=%d bytes, iv=%d bytes, %d CLU(s)",
        len(project.cipher_key), len(project.cipher_iv), len(project.clus),
    )
    for clu in project.clus:
        _LOGGER.info("  CLU %s  ip=%s  object=%s", clu.serial, clu.ip, clu.object_name)

    # Pick the target CLU.
    if args.clu:
        clu = project.clu(args.clu)
        if clu is None:
            _LOGGER.error("no CLU matching %r in the project", args.clu)
            return 2
    else:
        clu = next((c for c in project.clus if c.ip), None)
        if clu is None:
            _LOGGER.error("no CLU with an IP address found in the project")
            return 2
    if not clu.ip:
        _LOGGER.error("selected CLU %s has no IP address", clu.serial)
        return 2

    cipher = (
        GrentonCipher.default()
        if args.default_key
        else GrentonCipher(project.cipher_key, project.cipher_iv)
    )
    host_ip = args.host_ip or detect_local_ip(clu.ip)
    object_name = args.object or clu.object_name

    _LOGGER.info("target CLU %s @ %s  (host_ip=%s, report_port=%d)",
                 clu.serial, clu.ip, host_ip, args.report_port)

    terminator = "" if args.no_newline else "\r\n"
    conn = GrentonCluConnection(
        clu_ip=clu.ip,
        cipher=cipher,
        host_ip=host_ip,
        report_port=args.report_port,
        terminator=terminator,
    )
    await conn.open()
    try:
        # ── Step 1: checkAlive (proves key + transport) ──────────────────────
        _LOGGER.info("→ checkAlive() ...")
        reply = await conn.check_alive(timeout=args.timeout)
        if reply is None:
            _LOGGER.error(
                "no reply. Either the CLU is unreachable, or the key/format is "
                "wrong. Try --default-key, --no-newline, or check the IP."
            )
            return 1
        _LOGGER.info("✓ CLU replied to checkAlive: %r  → key + transport WORK", reply)

        if args.no_subscribe:
            return 0

        # ── Step 2: subscribe to the CLU's own features, print live pushes ────
        indices = parse_indices(args.indices)
        keys = [(object_name, i) for i in indices]
        session = secrets.randbelow(60000) + 1
        _LOGGER.info(
            "→ clientRegister session=%d object=%s indices=%s",
            session, object_name, indices,
        )

        report_count = 0

        def on_report(sess: int, values: list, resp: Response) -> None:
            nonlocal report_count
            report_count += 1
            tag = "ack" if not resp.is_notification else "push"
            _LOGGER.info("← clientReport[%s] session=%d: %s", tag, sess, values)

        initial = await conn.client_register(keys, session, on_report, timeout=args.timeout)
        if initial is None:
            _LOGGER.warning(
                "no clientReport ack. The object name (%s) or indices may be "
                "wrong. Try --object <name> / --indices, or a device object.",
                object_name,
            )
        _LOGGER.info("listening for pushes for %ds (Ctrl-C to stop) ...", args.duration)
        await asyncio.sleep(args.duration)
        _LOGGER.info("received %d report(s) total", report_count)

        await conn.client_destroy(session)
        _LOGGER.info("✓ clientDestroy sent — no subscription left behind")
        return 0
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--omp", required=True, help="path to the Object Manager .omp")
    p.add_argument("--clu", help="target CLU by serial or IP (default: first with an IP)")
    p.add_argument("--host-ip", help="this machine's LAN IP (default: auto-detect)")
    p.add_argument("--object", help="object name to subscribe to (default: CLU<serial>)")
    p.add_argument("--indices", default="0-15", help="feature indices, e.g. '0-15' or '0,1,2'")
    p.add_argument("--report-port", type=int, default=DEFAULT_REPORT_PORT)
    p.add_argument("--duration", type=int, default=20, help="seconds to listen for pushes")
    p.add_argument("--timeout", type=float, default=5.0, help="per-request timeout (s)")
    p.add_argument("--no-newline", action="store_true", help="omit the \\r\\n request terminator")
    p.add_argument("--default-key", action="store_true", help="use the factory default AES key")
    p.add_argument("--no-subscribe", action="store_true", help="only run checkAlive")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging (shows raw wire)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
