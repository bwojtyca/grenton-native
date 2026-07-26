"""HA-free event model for the live communication monitor.

A ``WireEvent`` is one thing that happened on the wire (a request we sent, a
response/report we received, an error, or a status change). ``EventRing`` keeps a
bounded, sequence-numbered history so a freshly-opened panel can back-fill and
then follow live. Kept dependency-free so it is unit-testable without Home
Assistant.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

# Wire event "kind" values (also used by the panel for colour/filtering).
KIND_REQUEST = "request"
KIND_RESPONSE = "response"
KIND_REPORT = "report"
KIND_ERROR = "error"
KIND_STATUS = "status"

DIRECTION_OUT = "out"
DIRECTION_IN = "in"


@dataclass(frozen=True)
class WireEvent:
    seq: int
    ts: float
    clu: str
    direction: str
    kind: str
    msg_id: str | None
    summary: str
    detail: Any = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventRing:
    """Bounded ring of WireEvents with a monotonic sequence counter."""

    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[WireEvent] = deque(maxlen=maxlen)
        self._seq = 0

    def add(
        self,
        *,
        ts: float,
        clu: str,
        direction: str,
        kind: str,
        summary: str,
        msg_id: str | None = None,
        detail: Any = None,
    ) -> WireEvent:
        self._seq += 1
        event = WireEvent(
            seq=self._seq,
            ts=ts,
            clu=clu,
            direction=direction,
            kind=kind,
            msg_id=msg_id,
            summary=summary,
            detail=detail,
        )
        self._events.append(event)
        return event

    @property
    def last_seq(self) -> int:
        return self._seq

    def snapshot(self) -> list[WireEvent]:
        return list(self._events)

    def snapshot_dicts(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self._events]
