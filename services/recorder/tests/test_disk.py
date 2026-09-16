from syntha_recorder.disk import Footprint, decide


def test_disk_cap_decisions() -> None:
    cap = 20_000
    assert decide(Footprint(db_bytes=5_000, raw_bytes=5_000), cap) == "ok"
    assert decide(Footprint(db_bytes=18_000, raw_bytes=5_000), cap) == "drop"  # chunks go first
    assert decide(Footprint(db_bytes=0, raw_bytes=25_000), cap) == "stop"  # raw is never deleted


class _FakeConn:
    """Refuses any execute outside `transaction()`: the bug of 2026-09-16 was a bare execute
    that opened an implicit transaction nothing committed, so every later write was lost."""

    def __init__(self) -> None:
        self.depth = 0
        self.statements: list[str] = []

    def transaction(self):
        conn = self

        class _Tx:
            def __enter__(self) -> None:
                conn.depth += 1

            def __exit__(self, *a: object) -> None:
                conn.depth -= 1

        return _Tx()

    def execute(self, sql: str, params: object = None):
        assert self.depth > 0, f"execute outside a transaction block: {sql[:40]}"
        self.statements.append(sql)
        statement = sql

        class _Cur:
            def fetchone(self) -> tuple[int] | None:
                return (0,) if "hypertable_size" in statement else None

            def fetchall(self) -> list[tuple[int]]:
                return []

        return _Cur()


def test_enforce_never_leaves_an_implicit_transaction_open(tmp_path) -> None:
    from syntha_recorder.disk import enforce

    conn = _FakeConn()
    assert enforce(conn, tmp_path, retention_days=30, cap_bytes=10**9) == "ok"  # type: ignore[arg-type]
    assert conn.depth == 0 and any("drop_chunks" in s for s in conn.statements)
