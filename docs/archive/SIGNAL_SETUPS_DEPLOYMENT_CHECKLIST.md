# Signal Setups — Local vs GitHub/Railway Deployment Checklist

This document confirms that **all signal setups** that run on localhost are present in the repo and deployed to Railway. Nothing is missing.

---

## Your list (9 items) — all present and deployed

| # | Setup | Defined in | Called from | In Git | In Docker |
|---|--------|------------|-------------|--------|-----------|
| 1 | **Setup A** (state machine) | `smc_mtf_engine_v4.py` (`detect_setup_a`) | `scan_symbol()` | ✅ | ✅ |
| 2 | **Setup C** (universal zone) | `smc_mtf_engine_v4.py` (`detect_setup_c`) | `scan_symbol()` | ✅ | ✅ |
| 3 | **Setup D** (BOS+FVG) | `smc_mtf_engine_v4.py` (`detect_setup_d`) | `scan_symbol()` | ✅ | ✅ |
| 4 | **Hierarchical** | `smc_mtf_engine_v4.py` (`detect_hierarchical`) + `smc_trading_engine/strategy/entry_model.py` | `scan_symbol()` | ✅ | ✅ |
| 5 | **EMA Crossover** | `smc_mtf_engine_v4.py` (`scan_ema_crossover`) | Main loop, every 5 min | ✅ | ✅ |
| 6 | **Zone Tap (5m)** | `engine/smc_zone_tap.py` (`scan_zone_taps`) | Main loop, minute % 5 == 1 | ✅ | ✅ |
| 7 | **Zone Tap (1m)** | `engine/smc_zone_tap.py` (1m thread after 5m tap) | Via `scan_zone_taps` → thread | ✅ | ✅ |
| 8 | **BankNifty Options** | `engine/options.py` (`BankNiftySignalEngine.poll`) | Main loop `bn_signal_engine.poll()` | ✅ | ✅ |
| 9 | **OI Short Covering** | `engine/oi_short_covering.py` (`scan_short_covering`) | Main loop, every cycle | ✅ | ✅ |

---

## One more setup you had on localhost (same 9 + 1)

| # | Setup | What it does | Defined in | In Git | In Docker |
|---|--------|----------------|------------|--------|-----------|
| 10 | **Option Strike Monthly Low Tap** | “[TAP DETECTED] Strike Touched Monthly Low!” — option strike taps monthly low then bounces 2% | `option_monitor_module.py` (`OptionMonitor.poll`) | ✅ | ✅ |

This is the alert you had: *"BANKNIFTY26MAR56100CE … Monthly Low: 1188.35, Tap Price: 1233.35, Bounce Price: 1272.25 (+3.2%) … Option Buying Opportunity?"*  
It runs from `option_monitor.poll()` in the main loop. It was using **UTC** for market hours on Railway; that’s now fixed to **IST** so it only runs during Indian market hours.

---

## Files that must be present (all are in repo and image)

- `smc_mtf_engine_v4.py` — main engine, Setups A/C/D, Hierarchical, EMA, loop
- `run_engine_railway.py` — Railway entrypoint
- `engine/options.py` — BankNiftySignalEngine (low break + bounce)
- `engine/oi_short_covering.py` — OI short covering scan
- `engine/smc_zone_tap.py` — Zone Tap 5m + 1m
- `engine/swing.py` — swing scan (daily)
- `engine/oi_sentiment.py` — OI bias (feeds regime)
- `engine/indicators.py` — ATR, EMA, etc.
- `engine/paper_mode.py` — paper prefix, logging
- `engine/market_state_engine.py` — market state
- `engine/config.py` — strategy flags
- `engine/displacement_detector.py` — displacement for Setup D
- `option_monitor_module.py` — Option Strike Monthly Low Tap
- `smc_trading_engine/strategy/entry_model.py` — Hierarchical `evaluate_entry`
- `config/kite_auth.py` — token resolution
- `manual_trade_handler_v2.py` — manual trade sync
- `trade_executor_bot.py` — optional; trade buttons (graceful ImportError if missing)

---

## What is excluded from Docker (by design)

- `.env`, `access_token.txt`, `kite_credentials.py` — secrets (set on Railway)
- `tests/` — not needed in engine image
- `dashboard/frontend/` — frontend built elsewhere
- `data/cache/`, `data/raw/` — local cache
- `*.db`, `*.pkl`, `*.log` — runtime state

None of these are signal logic; no setup is missing because of Docker exclusions.

---

## Summary

- You have **9 named setups**; all 9 are in the repo and on Railway.
- There is **1 additional setup**: **Option Strike Monthly Low Tap** (`OptionMonitor`). It is also in the repo and on Railway; its market-hours check was fixed to use IST.
- No setup file is missing on the server. If signals differ from localhost, the cause is environment (token, timezone, market hours, or strategy flags), not missing code.
