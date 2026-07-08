# Portfolio Risk Engine

`services/risk_engine.py` — a configurable, reversible risk layer for portfolio
**promotions** (sizing + acceptance) and **exits** (trend-break). Every component
is independently flag-gated and reads its configuration **live from the
environment**, so any part can be turned off **without a redeploy**, instantly
reverting to the legacy behavior.

## Why (evidence)

From a 2026-07 historical simulation over 58 completed trades (`docs/` / journal):

| Change | Profit factor | Max drawdown |
|---|---|---|
| Current (equal weight, no cap, stale-cull) | 1.25 | −52.9% |
| **Stop-cap + risk-normalized sizing** | **1.56–1.68** | **−11.1%** |
| Trend-break exit (200-DMA + RS<0) | 1.30 | −45.1% (0 winners hurt) |
| Fixed max-loss exit | 1.14–1.25 (worse/neutral) | — (stops winners) |

Validation re-run with the implemented engine: **PF 1.25 → 1.56, MaxDD −52.9% →
−11.1%**, and with all flags OFF the engine reproduces the legacy baseline
exactly (regression-safe).

## Components

### 1. Risk-normalized position sizing (default)
Each position is sized so it risks a **fixed fraction of capital**, not an equal
slice of capital:

```
risk_amount      = CAPITAL × RISK_PER_TRADE_PCT/100
risk_per_share   = entry − stop
position_value   = (risk_amount / risk_per_share) × entry
position_value   = position_value × liquidity_factor × atr_factor   # (4)
position_value   = min(position_value, CAPITAL × RISK_MAX_WEIGHT_PCT/100)
```

A 4%-stop name gets ~2× the notional of an 8%-stop name; a 39%-stop name (e.g.
ONMOBILE) gets a fractional slot, so a −10% move costs ~−1.3% at book level
instead of −10%. This is the single biggest drawdown lever.

### 2. Stop-width cap
Rejects a promotion whose stop is wider than the (horizon-specific) cap. Evidence
puts the swing sweet spot at 8–10%; the default **10% (swing) / 15% (long-term)**
kills the absurd 30–40% tail (which was net-losing) while letting sizing handle
the merely-wide names.

### 3. Trend-break exit
A held position exits when it has **decisively lost its 200-DMA** (`cmp <
200DMA × (1 − buffer)`) **and** turned **RS-negative vs NIFTY**. This supersedes
the legacy stale "dead-zone" cull when enabled. In backtest it improved PF +
drawdown and hurt **zero** winners (winners don't break their 200-DMA). A fixed
max-loss exit was explicitly rejected by the evidence.

### 4. Liquidity- / ATR-aware down-sizing
Rather than rejecting volatile/illiquid names outright, their size is **reduced**
(never below `LIQ_MIN_SIZE_FACTOR`):

```
atr_factor = ATR_SIZE_REF_PCT / atr_pct        if atr_pct > ATR_SIZE_REF_PCT
liquidity_factor = turnover_cr / LIQ_MIN_TURNOVER_CR   if turnover_cr < LIQ_MIN_TURNOVER_CR
```

Works together with (1). ATR% and daily turnover are fetched best-effort and
cached once per IST day.

## Decision flow

```
promote_to_portfolio(symbol, horizon, entry, stop, …)
        │
        ▼
risk_engine.evaluate_promotion()
   ├─ RISK_ENGINE_ENABLED = 0 ─────────────► accept, EQUAL-WEIGHT size (legacy)
   ├─ invalid geometry (stop ≥ entry) ─────► REJECT
   ├─ STOP_CAP_ENABLED & stop% > cap ──────► REJECT  (reason: stop_too_wide)
   ├─ RISK_SIZING_ENABLED = 0 ─────────────► accept, EQUAL-WEIGHT size
   └─ else ─ risk-normalized size × liquidity_factor × atr_factor, capped
                              │
                              ▼
               add_position(… position_size, risk_weight_pct, atr_pct, turnover_cr)

price tracker tick (per ACTIVE position, store permits auto-cull)
   ├─ cmp ≥ target ─► TARGET_HIT
   ├─ cmp ≤ stop ──► STOP_HIT
   └─ else:
        ├─ TREND_BREAK_EXIT_ENABLED ─► evaluate_trend_break_exit() ─► TREND_BREAK / hold
        └─ else (rollback) ─────────► legacy STRUCTURE_BREAK / STALE_EXIT
```

## Configuration (env vars, evidence-based defaults)

| Variable | Default | Purpose |
|---|---|---|
| `RISK_ENGINE_ENABLED` | `1` | Master switch. `0` = full legacy behavior. |
| `RISK_SIZING_ENABLED` | `1` | Risk-normalized sizing (else equal-weight). |
| `STOP_CAP_ENABLED` | `1` | Enforce stop-width caps. |
| `LIQUIDITY_ADJ_ENABLED` | `1` | Liquidity/ATR down-sizing. |
| `TREND_BREAK_EXIT_ENABLED` | `1` | Trend-break exit (supersedes stale-cull). |
| `PORTFOLIO_NOTIONAL_CAPITAL` | `1000000` | Notional capital for sizing math. |
| `RISK_PER_TRADE_PCT` | `1.0` | Risk budget per trade (% of capital). |
| `RISK_MAX_WEIGHT_PCT` | `15.0` | Max single-position weight. |
| `MAX_STOP_PCT` | `10.0` | Swing stop-width cap (%). |
| `MAX_STOP_LONGTERM_PCT` | `15.0` | Long-term stop-width cap (%). |
| `ATR_SIZE_REF_PCT` | `4.0` | ATR% above which size is scaled down. |
| `LIQ_MIN_TURNOVER_CR` | `2.0` | ₹Cr/day below which size is scaled down. |
| `LIQ_MIN_SIZE_FACTOR` | `0.15` | Floor for liquidity/ATR shrink. |
| `TREND_BREAK_MIN_DAYS` | `3` | Min holding days before trend-break can fire. |
| `TREND_BREAK_DMA_BUFFER` | `0.02` | How far below 200-DMA counts as a break. |
| `TREND_BREAK_RS_LOOKBACK` | `20` | RS lookback (days) vs NIFTY. |

> Defaults are the **evidence band**, not gospel. They were fit on 58 trades —
> re-validate as the sample grows and adjust via env (no redeploy).

## Rollback

Each component is independently disable-able live (set the env var, no deploy):

| To disable | Set |
|---|---|
| Risk sizing (→ equal weight) | `RISK_SIZING_ENABLED=0` |
| Stop-width cap | `STOP_CAP_ENABLED=0` |
| Trend-break exit (→ legacy stale-cull) | `TREND_BREAK_EXIT_ENABLED=0` |
| Liquidity/ATR adjustment | `LIQUIDITY_ADJ_ENABLED=0` |
| **Everything (full legacy)** | `RISK_ENGINE_ENABLED=0` |

## Audit log

Every promotion and exit decision is logged (`[RiskEngine][PROMOTE]` /
`[RiskEngine][EXIT]`) with the full record — old vs new decision, size, stop
width, ATR%, liquidity, trend status, RS, reason, and the active flags — and
best-effort persisted to Redis lists `risk_engine:promotions:{date}` /
`risk_engine:exits:{date}` (30-day TTL) for later audit.

## Storage

`portfolio_positions` gains nullable columns (added in-place, legacy-safe):
`position_size`, `risk_weight_pct`, `atr_pct`, `turnover_cr`.
