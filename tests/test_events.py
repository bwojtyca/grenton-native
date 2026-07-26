from custom_components.grenton_native.native.events import (
    DIRECTION_IN,
    DIRECTION_OUT,
    KIND_REPORT,
    KIND_REQUEST,
    EventRing,
)


def test_ring_assigns_monotonic_seq():
    ring = EventRing(maxlen=10)
    e1 = ring.add(
        ts=1.0, clu="A", direction=DIRECTION_OUT, kind=KIND_REQUEST, summary="checkAlive()"
    )
    e2 = ring.add(
        ts=2.0, clu="A", direction=DIRECTION_IN, kind=KIND_REPORT, summary="clientReport:1:{1}"
    )
    assert (e1.seq, e2.seq) == (1, 2)
    assert ring.last_seq == 2


def test_ring_is_bounded():
    ring = EventRing(maxlen=3)
    for i in range(5):
        ring.add(ts=float(i), clu="A", direction=DIRECTION_OUT, kind=KIND_REQUEST, summary=str(i))
    snap = ring.snapshot()
    assert [e.summary for e in snap] == ["2", "3", "4"]  # oldest dropped
    assert ring.last_seq == 5  # seq keeps counting


def test_snapshot_dicts_are_json_friendly():
    ring = EventRing()
    ring.add(
        ts=1.5, clu="CLU1", direction=DIRECTION_IN, kind=KIND_REPORT,
        summary="clientReport:7:{1,2}", msg_id="00000000", detail=[1, 2],
    )
    (d,) = ring.snapshot_dicts()
    assert d == {
        "seq": 1,
        "ts": 1.5,
        "clu": "CLU1",
        "direction": DIRECTION_IN,
        "kind": KIND_REPORT,
        "msg_id": "00000000",
        "summary": "clientReport:7:{1,2}",
        "detail": [1, 2],
    }
