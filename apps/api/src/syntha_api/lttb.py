"""Largest-Triangle-Three-Buckets downsampling (PRD §14: never ship 500k points to the browser)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def lttb_indices(
    x: npt.NDArray[np.float64], y: npt.NDArray[np.float64], threshold: int
) -> npt.NDArray[np.int64]:
    """Indices of the points to keep, always including the first and last."""
    n = x.size
    if threshold >= n or threshold < 3:
        return np.arange(n, dtype=np.int64)
    keep = np.empty(threshold, dtype=np.int64)
    keep[0], keep[-1] = 0, n - 1
    bucket = (n - 2) / (threshold - 2)
    a = 0
    for i in range(threshold - 2):
        start = int(np.floor(i * bucket)) + 1
        end = int(np.floor((i + 1) * bucket)) + 1
        nxt_start, nxt_end = end, min(int(np.floor((i + 2) * bucket)) + 1, n)
        avg_x = x[nxt_start:nxt_end].mean() if nxt_end > nxt_start else x[-1]
        avg_y = y[nxt_start:nxt_end].mean() if nxt_end > nxt_start else y[-1]
        xs, ys = x[start:end], y[start:end]
        area = np.abs((x[a] - avg_x) * (ys - y[a]) - (x[a] - xs) * (avg_y - y[a]))
        a = start + int(np.argmax(area))
        keep[i + 1] = a
    return keep


def equity_indices(
    x: npt.NDArray[np.float64],
    equity: npt.NDArray[np.float64],
    drawdown: npt.NDArray[np.float64],
    threshold: int,
) -> npt.NDArray[np.int64]:
    """LTTB plus the points a reader checks the chart against: peak equity, lowest equity and the
    max-drawdown trough. May return up to three points more than `threshold`."""
    if x.size <= threshold:
        return np.arange(x.size, dtype=np.int64)
    extremes = [int(np.argmax(equity)), int(np.argmin(equity)), int(np.argmax(drawdown))]
    return np.union1d(lttb_indices(x, equity, threshold), extremes).astype(np.int64)
