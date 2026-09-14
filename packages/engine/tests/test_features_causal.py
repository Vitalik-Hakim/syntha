"""Every registered feature is causal: its value at bar i is unchanged when all data after bar i's
close is deleted. This is what makes the vectorized entry path look-ahead-safe (PRD §5.1)."""

from decimal import Decimal

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from syntha_contracts.strategy import FeatureName
from syntha_engine.data import MarketTerms, TokenData
from syntha_engine.grid import Grid, build_frame
from syntha_engine.strategy import features as F

T0 = 1_767_225_600
TERMS = MarketTerms(
    "c", "e", "test", Decimal("0.01"), Decimal("5"), T0, T0, T0 + 86_400, False, None, None, None
)
PARAMS = {
    "price_change_pct": {"lookback_minutes": 3},
    "price_change_abs": {"lookback_minutes": 2},
    "traded_volume": {"lookback_minutes": 4},
    "volume_ratio": {"lookback_minutes": 2, "baseline_minutes": 6},
    "print_count": {"lookback_minutes": 3},
    "vwap_gap": {"lookback_minutes": 3},
    "realized_vol": {"lookback_minutes": 4},
}


def test_registry_matches_the_contract() -> None:
    assert set(F.REGISTRY) == set(FeatureName.__args__)  # type: ignore[attr-defined]


@st.composite
def token(draw: st.DrawFn) -> TokenData:
    n = draw(st.integers(5, 40))
    minutes = sorted(draw(st.sets(st.integers(0, 60), min_size=1, max_size=n)))
    prices = [draw(st.integers(1, 99)) / 100 for _ in minutes]
    m = draw(st.integers(0, 30))
    pr_ts = sorted(T0 + draw(st.integers(0, 3659)) for _ in range(m))
    return TokenData(
        "tok",
        "c",
        0,
        "Yes",
        np.array([T0 + x * 60 for x in minutes], dtype=np.int64),
        np.array(prices),
        np.array(pr_ts, dtype=np.int64),
        np.array([draw(st.integers(1, 99)) / 100 for _ in pr_ts]),
        np.array([float(draw(st.integers(1, 500))) for _ in pr_ts]),
        np.ones(m, dtype=np.int8),
    )


@settings(max_examples=150, deadline=None)
@given(token(), st.integers(0, 60))
def test_features_are_causal(tok: TokenData, cut: int) -> None:
    grid = Grid.build(T0, T0 + 60 * 60, 1)
    full = build_frame(tok, TERMS, grid)
    close = grid.close(cut)
    b, p = tok.bar_ts < close, tok.print_ts < close
    trunc_tok = TokenData(
        "tok",
        "c",
        0,
        "Yes",
        tok.bar_ts[b],
        tok.bar_price[b],
        tok.print_ts[p],
        tok.print_price[p],
        tok.print_size[p],
        tok.print_side[p],
    )
    part = build_frame(trunc_tok, TERMS, grid)
    if full is None or part is None or full.k0 > cut:
        return
    assert part.k0 == full.k0
    upto = cut - full.k0 + 1
    for name in F.REGISTRY:
        params = PARAMS.get(name, {})
        a = F.compute(full, name, params)[:upto]
        z = F.compute(part, name, params)
        z = np.concatenate([z, np.full(max(0, upto - z.size), np.nan)])[:upto]
        # truncated frames may end before `cut`; bars beyond their data are simply absent
        n = min(part.n, upto)
        np.testing.assert_allclose(a[:n], z[:n], equal_nan=True, err_msg=name)
