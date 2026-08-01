"""
services/scanners/ — Technical scanner suite (producer side).

Pure, deterministic scanners that run in a SEPARATE cron worker
(`scripts/scanner_cron.py`), never on the web request path. Results are written
to Redis snapshots and served read-only by `dashboard/backend/routes/screeners.py`.

Modules:
  indicators.py     — pure math: supertrend(), ema(), atr() (unit-tested)
  universe.py       — curated liquid NSE universe (default) + full opt-in
  scoring.py        — confluence quality_score + tiering
  registry.py       — Scanner definitions (add a scanner = add one entry)
  data_layer.py     — bulk OHLC fetch (Kite, rate-limit aware)
  runner.py         — orchestrator: fetch once → run all → score → write snapshot
  snapshot_store.py — Redis write (non-empty gate + LKG) / read helpers

Design invariant: NOTHING here is imported by the live trading engine loop.
The web backend only imports snapshot_store (read path).
"""
