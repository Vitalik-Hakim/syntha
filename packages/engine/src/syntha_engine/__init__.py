"""Syntha backtest engine.

Hard boundary (PRD §6): this package performs no I/O. It takes a BacktestRequest plus an
in-memory Dataset and returns a BacktestResult. tools/check_engine_io.py fails CI on any
network, database, filesystem or subprocess import in this tree.
"""

ENGINE_VERSION = "0.6.0"  # M6: min-order policy, capacity blocks (see DECISIONS)

__all__ = ["ENGINE_VERSION"]
