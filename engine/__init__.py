"""
SMC MTF Engine v4 — Modular Package
====================================
Phase 5 modularisation of the 5400-line monolith.

Modules
-------
config               – constants, strategy flags, global mutable state
indicators           – ATR, EMA, ADX, volume, liquidity, killzone
swing                – swing scanner (weekly + daily analysis)
options              – LiveTickStore, BankNiftySignalEngine
paper_mode           – Phase 6: paper trading CSV logger & validation
oi_sentiment         – OI-based market sentiment (PCR, OI change, buildup)
liquidity_engine     – Liquidity sweep & level detection
market_state_engine  – Real-time market state transition engine
displacement_detector– Phase 2: Early institutional displacement detection
                       (detects momentum candles 30-40 min before CHoCH)
"""
