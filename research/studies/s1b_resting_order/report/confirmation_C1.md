## s1b_resting_order on C1 (2026-06, 2026-07, 2026-08)

Generated 2026-09-16T17:54:27+00:00; 756,260 taker-BUY prints loaded (203,555 outside ultra_short). Presets: realistic = fill at the print price (deciding); conservative = one tick worse for the maker.

| # | Cell | preset | n | events | loss events (prints) | r_maker | 90% CI (key) | size-wtd | event-wtd | rebate 25% | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | politics [0.20,0.35) -6h | realistic | 55 | 19 | 5 (23) | -0.181 | -0.589 to +0.307 (event) | -0.943 | +0.009 | -0.178 | +0.003 | +0.023 | 0.675 | 2/3 | $51 | underpowered |
| 1 | politics [0.20,0.35) -6h | conservative | 55 | 19 | 5 (23) | -0.193 | -0.596 to +0.283 (event) | -0.943 | -0.005 | -0.190 | -0.011 | +0.008 | 0.663 | 2/3 | $51 |  |
| 2 | crypto [0.03,0.10) -15m | realistic | 1,689 | 302 | 16 (54) | +0.031 | +0.008 to +0.048 (event) | +0.042 | +0.010 | +0.032 | -0.001 | +0.030 | 0.030 | 3/3 | $1,161 | pass |
| 2 | crypto [0.03,0.10) -15m | conservative | 1,689 | 302 | 16 (54) | +0.020 | -0.002 to +0.037 (event) | +0.031 | -0.001 | +0.021 | -0.012 | +0.019 | 0.029 | 2/3 | $1,166 |  |
| 3 | politics [0.03,0.10) -1h | realistic | 70 | 29 | 1 (2) | +0.034 | -0.028 to +0.070 (event) | +0.042 | +0.030 | +0.035 | +0.013 | +0.002 | 0.079 | 3/3 | $86 | underpowered; no tail observed (1 loss events) |
| 3 | politics [0.03,0.10) -1h | conservative | 70 | 29 | 1 (2) | +0.024 | -0.038 to +0.058 (event) | +0.031 | +0.019 | +0.024 | +0.002 | -0.009 | 0.079 | 2/3 | $87 | ; no tail observed (1 loss events) |
| 4 | weather [0.03,0.10) -1h | realistic | 198 | 103 | 4 (5) | +0.036 | +0.005 to +0.059 (day) | +0.026 | +0.028 | +0.036 | -0.002 | +0.006 | 0.041 | 2/3 | $219 | underpowered; no tail observed (4 loss events) |
| 4 | weather [0.03,0.10) -1h | conservative | 198 | 103 | 4 (5) | +0.025 | -0.006 to +0.048 (day) | +0.016 | +0.017 | +0.025 | -0.012 | -0.004 | 0.041 | 2/3 | $221 | ; no tail observed (4 loss events) |
| 5 | politics [0.01,0.03) -1h | realistic | 27 | 15 | 0 (0) | +0.020 | +0.017 to +0.023 (event) | +0.017 | +0.019 | +0.020 | +0.002 | +0.000 | 0.005 | 2/2 | $51 | underpowered; no tail observed (0 loss events) |
| 5 | politics [0.01,0.03) -1h | conservative | 27 | 15 | 0 (0) | +0.010 | +0.008 to +0.013 (event) | +0.008 | +0.010 | +0.010 | -0.007 | -0.009 | 0.004 | 2/2 | $52 | ; no tail observed (0 loss events) |

Splits, realistic preset:

| # | Cell | event-wtd | 2025 H2 (n) | 2026 (n) | BY (n) | ON (n) | NONE (n) |
|---|---|---|---|---|---|---|---|
| 1 | politics [0.20,0.35) -6h | +0.009 | — (0) | — (0) | +0.134 (5) | +0.296 (4) | -0.257 (46) |
| 2 | crypto [0.03,0.10) -15m | +0.010 | — (0) | — (0) | — (0) | +0.054 (395) | +0.023 (1,294) |
| 3 | politics [0.03,0.10) -1h | +0.030 | — (0) | — (0) | +0.075 (1) | +0.076 (3) | +0.032 (66) |
| 4 | weather [0.03,0.10) -1h | +0.028 | — (0) | — (0) | — (0) | +0.036 (198) | — (0) |
| 5 | politics [0.01,0.03) -1h | +0.019 | — (0) | — (0) | +0.030 (1) | +0.016 (2) | +0.020 (24) |
