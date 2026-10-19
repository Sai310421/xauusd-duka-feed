# AE Math Paper Watch -> AMOS validation contract v1

Source: AE Math Paper Watch research history (research candidates, NOT validated trading EDGE).
Do not change existing Builder-66 feature schema, frozen trading entry, direction, exits, or existing strategy baselines.

## Research lanes
1. ENTRY_EDGE: economic rationale and original paper equations -> independent implementation -> after-cost signal test.
2. RISK_EDGE: original bot frozen; insert only external exposure/lot/add governor and measure causal differences.
3. EXECUTION_EDGE: realistic slippage, spread, queue/fill assumptions and costs.
4. INSURANCE_EDGE: protective put / hybrid put-trend / capital constraints; require instrument and premium data, especially IBKR where applicable.

## Candidate register
- Dynamic DDR + continuous First-Passage: RISK_EDGE
- Dynamic CVaR / Wasserstein DRO: RISK_EDGE
- Azema-Yor drawdown transform: RISK_EDGE
- Kelly / conformal confidence bounds: RISK_EDGE
- Almgren-Chriss / MPC execution: EXECUTION_EDGE
- Hawkes shocks / microstructure waiting option: ENTRY_EDGE / EXECUTION_EDGE (market depth data may be required)
- Hybrid Put/Trend / Parisian ruin / physical crash frontier: INSURANCE_EDGE
- ES-only E-backtesting / HE-CV-UCB / Omega bankruptcy model: diagnostics and candidate selection

## Evidence and promotion
- PAPER_ORIGINAL: original equations and assumptions; record citation and publication date.
- AE_DERIVED: explicitly separate any trading adaptation from paper claims.
- IMPLEMENTED: deterministic tests, numerical sanity and negative controls.
- RAW_TICK: 90-day minimum exploratory, 365-day standard, 730-day long OOS where data permits; Bid/Ask, costs, slippage, margin.
- AB_PARITY: identical data, order path and execution assumptions; freeze base strategy for DD-only experiments.
- OOS_WFO: purged chronological splits, embargo, untouched final holdout, walk-forward and block bootstrap/Monte Carlo.
- KPI: PF, RF, EV, Max Floating DD, tail risk, margin level, ruin probability estimate with uncertainty, turnover and fees.
- REJECT if costs erase EDGE, OOS unstable, data mismatch, forward leakage or unsupported paper assumptions.
- PROMOTE only on independently reproducible results and preserved artifacts. No automated live trading or auto-merge.

## Integration
Data -> causal feature families (Builder 66, Fib/XABCD, paper-specific) -> isolated candidate strategies/governors -> Nautilus QuoteTick -> OOS/WFO/Monte Carlo -> evidence -> Virtual Entry / Shadow / Demo.

Status: research-to-validation specification committed; no Math Paper candidate is thereby certified profitable.
