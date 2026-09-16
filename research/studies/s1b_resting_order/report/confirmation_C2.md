## s1b_resting_order on C2 (2026-01)

Generated 2026-09-16T17:55:40+00:00; 454,943 taker-BUY prints loaded (73,010 outside ultra_short). Presets: realistic = fill at the print price (deciding); conservative = one tick worse for the maker.

| # | Cell | preset | n | events | loss events (prints) | r_maker | 90% CI (key) | size-wtd | event-wtd | rebate 25% | AS 5m | AS 30m | MDE@80% | months + | capacity $1k/mo | set rule |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | politics [0.20,0.35) -6h | realistic | 102 | 15 | 4 (19) | +0.166 | -0.135 to +0.383 (event) | +0.275 | +0.096 | +0.169 | -0.003 | -0.002 | 0.392 | 1/1 | $230 | underpowered; no tail observed (4 loss events) |
| 1 | politics [0.20,0.35) -6h | conservative | 102 | 15 | 4 (19) | +0.150 | -0.150 to +0.359 (day) | +0.257 | +0.081 | +0.152 | -0.017 | -0.016 | 0.389 | 1/1 | $231 | ; no tail observed (4 loss events) |
| 2 | crypto [0.03,0.10) -15m | realistic | 832 | 163 | 5 (24) | +0.031 | -0.011 to +0.058 (event) | -0.027 | +0.025 | +0.032 | -0.011 | +0.026 | 0.056 | 1/1 | $2,001 | fail |
| 2 | crypto [0.03,0.10) -15m | conservative | 832 | 163 | 5 (24) | +0.020 | -0.023 to +0.047 (event) | -0.037 | +0.014 | +0.021 | -0.021 | +0.015 | 0.056 | 1/1 | $2,008 |  |
| 3 | politics [0.03,0.10) -1h | realistic | 151 | 10 | 0 (0) | +0.061 | +0.051 to +0.078 (event) | +0.062 | +0.057 | +0.062 | +0.002 | +0.008 | 0.020 | 1/1 | $229 | underpowered; no tail observed (0 loss events) |
| 3 | politics [0.03,0.10) -1h | conservative | 151 | 10 | 0 (0) | +0.050 | +0.041 to +0.066 (event) | +0.051 | +0.046 | +0.051 | -0.009 | -0.003 | 0.020 | 1/1 | $230 | ; no tail observed (0 loss events) |
| 4 | weather [0.03,0.10) -1h | realistic | 67 | 44 | 3 (3) | +0.018 | -0.033 to +0.059 (event) | -0.014 | +0.001 | +0.019 | -0.004 | +0.000 | 0.070 | 1/1 | $291 | underpowered; no tail observed (3 loss events) |
| 4 | weather [0.03,0.10) -1h | conservative | 67 | 44 | 3 (3) | +0.008 | -0.044 to +0.047 (event) | -0.024 | -0.009 | +0.008 | -0.014 | -0.011 | 0.069 | 1/1 | $294 | ; no tail observed (3 loss events) |
| 5 | politics [0.01,0.03) -1h | realistic | 172 | 9 | 0 (0) | +0.017 | +0.016 to +0.020 (event) | +0.016 | +0.018 | +0.017 | +0.006 | +0.014 | 0.003 | 1/1 | $203 | underpowered; no tail observed (0 loss events) |
| 5 | politics [0.01,0.03) -1h | conservative | 172 | 9 | 0 (0) | +0.008 | +0.007 to +0.011 (event) | +0.007 | +0.009 | +0.008 | -0.004 | +0.005 | 0.003 | 1/1 | $204 | ; no tail observed (0 loss events) |

Splits, realistic preset:

| # | Cell | event-wtd | 2025 H2 (n) | 2026 (n) | BY (n) | ON (n) | NONE (n) |
|---|---|---|---|---|---|---|---|
| 1 | politics [0.20,0.35) -6h | +0.096 | — (0) | — (0) | — (0) | +0.389 (2) | +0.162 (100) |
| 2 | crypto [0.03,0.10) -15m | +0.025 | — (0) | — (0) | +0.053 (1) | +0.032 (557) | +0.028 (274) |
| 3 | politics [0.03,0.10) -1h | +0.057 | — (0) | — (0) | +0.082 (1) | — (0) | +0.061 (150) |
| 4 | weather [0.03,0.10) -1h | +0.001 | — (0) | — (0) | — (0) | +0.018 (66) | +0.036 (1) |
| 5 | politics [0.01,0.03) -1h | +0.018 | — (0) | — (0) | — (0) | — (0) | +0.017 (172) |
