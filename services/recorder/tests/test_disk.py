from syntha_recorder.disk import Footprint, decide


def test_disk_cap_decisions() -> None:
    cap = 20_000
    assert decide(Footprint(db_bytes=5_000, raw_bytes=5_000), cap) == "ok"
    assert decide(Footprint(db_bytes=18_000, raw_bytes=5_000), cap) == "drop"  # chunks go first
    assert decide(Footprint(db_bytes=0, raw_bytes=25_000), cap) == "stop"  # raw is never deleted
