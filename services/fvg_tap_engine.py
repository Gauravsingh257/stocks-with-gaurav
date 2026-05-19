"""
services/fvg_tap_engine.py
==========================
FVG-Tap live tick — index 5m only (NIFTY + BANKNIFTY). Mirrors the
PROVEN, isolated G2-6 pattern (services/equity_state_machine.py) so it
can never disturb the live index engine or its Telegram worker.

Flag `FVG_TAP_MODE` (engine.fvg_tap_setup.fvg_tap_mode):
  off    → never invoked (byte-identical engine)
  shadow → log every would-fire to the lifecycle ledger ONLY
  alert  → shadow + isolated Telegram + an isolated FVG_TAP
           recommendation (website surface). NOT auto-traded.
  live   → reserved for real auto-execution; intentionally still
           behaves as `alert` until the forward-shadow scorecard
           clears (per the pre-committed discipline — a ~60d/one-regime
           backtest PASS earns shadow+alert, not auto-trade).

Data source = yfinance ^NSEI / ^NSEBANK 5m — the SAME data the rule was
validated on (NIFTY 5m PF 2.07, BANKNIFTY 5m PF 2.33), so live
evaluation is apples-to-apples with the gate. Best-effort: every failure
is swallowed; this module can never raise into a caller.
"""

from __future__ import annotations

import logging

from engine.fvg_tap_setup import evaluate_fvg_tap, fvg_tap_mode, INDEX_SYMBOLS

log = logging.getLogger("fvg_tap_engine")

_YF = {"NSE:NIFTY 50": "^NSEI", "NSE:NIFTY BANK": "^NSEBANK"}
_AGENT_TYPE = "FVG_TAP"
_SEEN: set[str] = set()  # in-process dedup (single scheduler process)


def _fetch_index_5m() -> dict:
    """{display_symbol: candles[]} for the two indices, 5m, ~60d.
    Same normalization as the validated backtest loader."""
    import yfinance as yf

    def _norm(df):
        if df is None or len(df) == 0:
            return []
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.droplevel(1, axis=1)
        try:
            idx = df.index.tz_convert("Asia/Kolkata")
        except (TypeError, AttributeError):
            idx = df.index
        out = []
        for ts, row in zip(idx, df.itertuples(index=False)):
            o, h, l, c = float(row.Open), float(row.High), float(row.Low), float(row.Close)
            if not (o > 0 and h > 0 and l > 0 and c > 0):
                continue
            out.append({"date": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": o, "high": h, "low": l, "close": c,
                        "volume": int(getattr(row, "Volume", 0) or 0)})
        return out

    data = {}
    for disp, tk in _YF.items():
        try:
            cs = _norm(yf.download(tk, interval="5m", period="60d",
                                   progress=False, auto_adjust=False))
            if len(cs) >= 80:
                data[disp] = cs
        except Exception as exc:
            log.warning("fvg_tap fetch %s failed: %s", tk, exc)
    return data


def _notify_fvg_tap_telegram(text: str) -> bool:
    """ONE message via a FULLY ISOLATED channel (`FVG_TAP_TG_TOKEN`,
    `FVG_TAP_TG_CHAT`). Never touches services.signal_delivery / the live
    worker. Opt-in: unset ⟹ silent no-op. Best-effort; never raises."""
    import os
    token = os.getenv("FVG_TAP_TG_TOKEN", "").strip()
    chat = os.getenv("FVG_TAP_TG_CHAT", "").strip()
    if not token or not chat:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=8,
        )
        return r.status_code == 200
    except Exception as exc:
        log.warning("fvg_tap telegram skipped: %s", exc)
        return False


def _publish_fvg_tap_recommendation(sym: str, sig: dict):
    """Persist ONE isolated FVG_TAP recommendation (distinct agent_type ⟹
    cannot collide with any other engine). entry_type MARKET (this setup
    enters market-on-confirmation). Best-effort; never raises."""
    try:
        from dashboard.backend.db import create_stock_recommendation

        row = {
            "symbol": sym,
            "agent_type": _AGENT_TYPE,
            "entry_price": sig["entry"],
            "stop_loss": sig["sl"],
            "targets": [sig["target"]],
            "confidence_score": 60.0,  # structural (fixed RR), not predictive
            "setup": "FVG_TAP",
            "entry_type": "MARKET",
            "scan_cmp": sig["entry"],
            "data_authenticity": "real",
            "expected_holding_period": "Intraday/swing (5m)",
            "technical_signals": [
                "5m_choch_gate", "bullish_fvg" if sig["direction"] == "LONG"
                else "bearish_fvg", "first_fvg_tap", "engulfing_confirmation",
            ],
            "reasoning": (
                "VALIDATION PHASE — FVG-Tap signal (index 5m). Backtest "
                "cleared the pre-committed index-domain gate: NIFTY 5m "
                "PF 2.07, BANKNIFTY 5m PF 2.33 (criteria locked before "
                "results). LIVE forward-validation in progress — NOT "
                "auto-traded, NOT a position. "
                f"{sig['direction']} MARKET-on-confirmation @ {sig['entry']}, "
                f"SL {sig['sl']} (beyond FVG {sig['fvg_low']}–{sig['fvg_high']}), "
                f"target {sig['target']}, RR {sig['rr']}. "
                "Confidence is structural (fixed RR), not predictive."
            ),
            "scan_run_id": None,
        }
        rec_id = create_stock_recommendation(row)
        if isinstance(rec_id, int) and rec_id > 0:
            _notify_fvg_tap_telegram(
                "🧪 <b>FVG-TAP SIGNAL</b> (validation — NOT auto-traded)\n"
                f"<b>{sym}</b>  {sig['direction']} · MARKET-on-confirmation\n"
                f"Entry <b>{sig['entry']}</b> · SL {sig['sl']} · "
                f"T {sig['target']} · RR {sig['rr']}\n"
                f"FVG {sig['fvg_low']}–{sig['fvg_high']} · confirmed "
                f"{sig.get('confirm_time')}\n"
                "<i>Index 5m. Backtest-cleared (NIFTY PF2.07 / BANKNIFTY "
                "PF2.33); live-validating. No position opened.</i>"
            )
        return rec_id
    except Exception as exc:
        log.warning("fvg_tap publish skipped (%s): %s", sym, exc)
        return None


def run_fvg_tap_tick() -> dict:
    """One isolated tick. Gated by FVG_TAP_MODE; off ⟹ instant no-op.
    Evaluates NIFTY+BANKNIFTY 5m both directions; dedups per confirmation
    candle; logs to the lifecycle ledger (shadow+) and publishes an
    isolated recommendation + Telegram (alert+). Never raises."""
    mode = fvg_tap_mode()
    if mode not in ("shadow", "alert", "live"):
        return {"status": "off"}
    publish = mode in ("alert", "live")
    out = {"status": "ok", "mode": mode, "evaluated": 0,
           "new_signals": 0, "published": 0}
    try:
        data = _fetch_index_5m()
        for sym in INDEX_SYMBOLS:
            candles = data.get(sym)
            if not candles:
                continue
            for direction in ("LONG", "SHORT"):
                out["evaluated"] += 1
                sig = evaluate_fvg_tap(candles, direction)
                if not sig:
                    continue
                key = f"{sym}|{direction}|{sig.get('confirm_time')}"
                if key in _SEEN:
                    continue
                _SEEN.add(key)
                if len(_SEEN) > 2000:
                    _SEEN.clear()
                out["new_signals"] += 1
                try:
                    from dashboard.backend.lifecycle_ledger import record_lifecycle_event
                    record_lifecycle_event(
                        sym, "ENTRY_ACTIVE", horizon="INTRADAY",
                        prev_state="ARMED", source="fvg_tap",
                        planned_entry=sig["entry"], stop_loss=sig["sl"],
                        target_1=sig["target"], rr_planned=sig["rr"],
                        setup="FVG_TAP",
                        details={"direction": sig["direction"],
                                 "fvg": [sig["fvg_low"], sig["fvg_high"]],
                                 "confirm_time": sig.get("confirm_time"),
                                 "mode": mode},
                    )
                except Exception as exc:
                    log.warning("fvg_tap ledger skipped: %s", exc)
                if publish:
                    rid = _publish_fvg_tap_recommendation(sym, sig)
                    if isinstance(rid, int) and rid > 0:
                        out["published"] += 1
        return out
    except Exception as exc:  # never propagate — isolated like G2-6
        log.warning("fvg_tap tick skipped: %s", exc)
        return {"status": "error", "detail": str(exc)}
