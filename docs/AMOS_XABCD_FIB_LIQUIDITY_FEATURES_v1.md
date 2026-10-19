# AMOS XABCD / Fibonacci / Liquidity Feature Research v1

Source: six user-supplied TradingView XAUUSD H2/H4 screenshots, July 29–August 18, 2026. Visual research specification, **not** a verified trading signal or reproduced indicator.

## Observed elements
- X-A-B-C-D zigzag / harmonic-like formation and potential bullish reversal from D.
- Candidate annotations: 0.654, 0.667, 0.697, 0.706, 0.825, 0.835, 1.255, 1.302.
- Upper liquidity target (DrawOnLiq), lower-timeframe last liquidity, QM H4/D1, TP1, TP2.
- Ratios' exact leg anchoring cannot be established from screenshots alone. Do not assert equivalence to standard harmonic ratios.

## Feature family (separate from frozen Builder 66 baseline)
- xabcd_pivot_confirmed, xabcd_leg_xa_atr, xabcd_leg_ab_xa, xabcd_leg_bc_ab, xabcd_leg_cd_bc, xabcd_age_bars
- fib_distance_to_0654_atr, fib_distance_to_0667_atr, fib_distance_to_0697_atr, fib_distance_to_0706_atr, fib_distance_to_0825_atr, fib_distance_to_0835_atr
- fib_extension_1255_distance_atr, fib_extension_1302_distance_atr
- d_zone_overlap_atr, d_zone_touch, d_rejection_strength_atr
- prior_low_sweep, prior_high_sweep, sweep_reclaim_bars, last_liquidity_distance_atr
- qm_h4_distance_atr, qm_d1_distance_atr, draw_on_liquidity_distance_atr
- tp1_distance_atr, tp2_distance_atr, reward_risk_tp1, reward_risk_tp2

## No-lookahead implementation contract
1. Derive bars from bid/ask QuoteTick with UTC boundaries and explicit timeframe calendars.
2. Confirm a pivot only after the requisite *future-to-pivot* confirmation bars have actually closed; the pivot feature becomes available at confirmation time, never retroactively at pivot time.
3. Use only information known at the feature timestamp, including H4/D1 pivots and liquidity levels.
4. Candidate levels are computed from explicit leg anchors; until anchors are established, report null/not_available rather than inferred values.
5. TP1/TP2 are derived from known levels at entry, not from subsequent realized highs.
6. Fit scaling, feature selection and threshold tuning only on training folds. Purged/embargoed chronological OOS/WFO; compare unchanged 66 baseline against 66+new family and selected subsets.
7. Evaluate Bid/Ask execution, MAE/MFE, costs, PF, RF, floating DD, and fold-by-fold stability; never label screenshots as evidence of profitability.
8. Separate liquidity proxies (swing levels/sweeps) from true L2 order-book liquidity, which requires independent market depth data.

## Research status
Feature specification registered. Numerical reproduction, image-leg annotation, GitHub Actions execution and Nautilus OOS validation not yet verified.
