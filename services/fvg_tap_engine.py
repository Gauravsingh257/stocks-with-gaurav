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
# How many recent closed bars the live tick treats as decision points, so a
# confirmation the */5 cron missed by one bar still fires (env-tunable).
_LIVE_LOOKBACK = max(1, int(__import__("os").getenv("FVG_TAP_LIVE_LOOKBACK", "2")))

# Module-cached KiteConnect client + instrument-token map. Mirrors the
# proven PR #3 pattern in agents/oi_intelligence_agent.py — one client
# per process, refreshed access_token on every call (so daily token
# rotation Just Works), urllib3 pool bumped to 50 to avoid the pool
# exhaustion we hit on the OI tick. Cache is private to this module
# (isolation by design — FVG-Tap engine never shares state with the
# live SMC engine or its Kite session).
_KITE_CACHE: dict = {"kite": None, "api_key": None}
_KITE_TOKEN_CACHE: dict[str, int] = {}


def _get_fvg_tap_kite_client():
    """Lazy module-cached KiteConnect for FVG-Tap fetch. Returns None if
    Kite is unavailable (no api_key, no token, init failure) — caller
    falls back to yfinance. Best-effort; never raises into the tick."""
    try:
        from config.kite_auth import get_access_token, get_api_key
        api_key = get_api_key()
        token = get_access_token()
        if not api_key or not token:
            return None
        cached = _KITE_CACHE.get("kite")
        if cached is None or _KITE_CACHE.get("api_key") != api_key:
            from kiteconnect import KiteConnect
            from requests.adapters import HTTPAdapter
            client = KiteConnect(api_key=api_key)
            try:
                adapter = HTTPAdapter(pool_connections=10, pool_maxsize=50)
                client.reqsession.mount("https://", adapter)
                client.reqsession.mount("http://", adapter)
            except AttributeError:
                pass
            _KITE_CACHE["kite"] = client
            _KITE_CACHE["api_key"] = api_key
            cached = client
        cached.set_access_token(token)
        return cached
    except Exception as exc:
        log.warning("fvg_tap kite client unavailable: %s", exc)
        return None


def _kite_instrument_token(kite, sym: str) -> int | None:
    """NSE:SYMBOL -> instrument_token. Result cached for process
    lifetime — index instrument tokens never change."""
    if sym in _KITE_TOKEN_CACHE:
        return _KITE_TOKEN_CACHE[sym]
    try:
        d = kite.ltp(sym)
        if not d:
            return None
        tok = int(list(d.values())[0]["instrument_token"])
        _KITE_TOKEN_CACHE[sym] = tok
        return tok
    except Exception as exc:
        log.warning("fvg_tap kite token lookup %s failed: %s", sym, exc)
        return None


def _fetch_index_5m_kite() -> dict:
    """Kite historical_data 5m for the two indices, ~60d each. Returns
    same dict shape as the yfinance path. Real-time (~5-30s lag for
    the just-closed bar) — validated equivalent to yfinance per the
    Phase 1 backtest: both indices clear the same locked gate on Kite
    OHLC that they cleared on yfinance OHLC, with Kite actually
    showing stronger PF (BANKNIFTY 4.00 vs 2.40). Filters Kite's
    after-hours padded bars (O==H==L==C, volume=0) so they cannot
    pollute the FVG/engulf logic. Returns {} on Kite unavailable so
    caller falls back to yfinance. Never raises."""
    from datetime import datetime, timedelta

    kite = _get_fvg_tap_kite_client()
    if kite is None:
        return {}

    out: dict = {}
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=60)
    for disp in _YF.keys():
        try:
            token = _kite_instrument_token(kite, disp)
            if token is None:
                continue
            raw = kite.historical_data(token, from_dt, to_dt, "5minute")
            cs: list = []
            for bar in raw:
                o = float(bar["open"]); h = float(bar["high"])
                lo = float(bar["low"]); c = float(bar["close"])
                if not (o > 0 and h > 0 and lo > 0 and c > 0):
                    continue
                vol = int(bar.get("volume", 0) or 0)
                # Skip Kite's after-hours padded bars (flat OHLC, no
                # volume). Real index bars practically never have all
                # four prices exactly equal at the precision Kite returns.
                if o == h == lo == c and vol == 0:
                    continue
                dt = bar["date"]
                ts = (dt.strftime("%Y-%m-%dT%H:%M:%S")
                      if hasattr(dt, "strftime") else str(dt))
                cs.append({"date": ts,
                           "open": round(o, 2), "high": round(h, 2),
                           "low": round(lo, 2), "close": round(c, 2),
                           "volume": vol})
            if len(cs) >= 80:
                out[disp] = cs
        except Exception as exc:
            log.warning("fvg_tap kite fetch %s failed: %s", disp, exc)
    return out


def _fetch_index_5m_yfinance() -> dict:
    """yfinance fallback path. Same logic as the original (pre-Kite-switch)
    function. Known to lag ~15+ min on free tier, which causes the strict
    'engulf must be last closed bar' detector rule to systematically miss
    signals (proven by 2026-05-22 forensic — 3 valid setups dropped). Use
    only when Kite client is unavailable (token expired, API down)."""
    import yfinance as yf

    def _norm(df):
        if df is None or len(df) == 0:
            return []
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.droplevel(1, axis=1)
        # Normalize the index to IST. yfinance may return either a tz-AWARE
        # index (convert directly) or a tz-NAIVE one (its intraday stamps are
        # UTC — localize to UTC first, THEN convert). The old code kept a
        # tz-naive index AS-IS, so a 15:05 IST bar was written as its raw UTC
        # clock "09:35", making confirm_time appear ~5.5h in the past and
        # poisoning the freshness telemetry (2026-06-08 forensic).
        idx = df.index
        try:
            if getattr(idx, "tz", None) is None:
                idx = idx.tz_localize("UTC").tz_convert("Asia/Kolkata")
            else:
                idx = idx.tz_convert("Asia/Kolkata")
        except (TypeError, AttributeError, ValueError):
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
            log.warning("fvg_tap yfinance fetch %s failed: %s", tk, exc)
    return data


def _fetch_index_5m() -> dict:
    """{display_symbol: candles[]} for the two indices, 5m, ~60d.

    Source priority (Phase 2 of the 2026-05-22 yfinance-lag fix):
      1. Kite historical_data — real-time (~5-30s lag), Phase 1
         backtest-validated equivalent (PR #5 — same locked gate
         clears on Kite OHLC, both indices, with stronger PF).
      2. yfinance fallback — only when Kite client unavailable
         (e.g. token expired); known to lag ~15+ min which causes
         the strict engulf-must-be-last-bar rule to miss signals.

    Emits a per-fetch freshness INFO line (`source=...` +
    `ages_sec=...`) so production logs can verify Kite is delivering
    real-time bars vs falling back to laggy yfinance."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        _ist = ZoneInfo("Asia/Kolkata")
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore
        _ist = ZoneInfo("Asia/Kolkata")

    data = _fetch_index_5m_kite()
    source = "kite"
    if not data:
        data = _fetch_index_5m_yfinance()
        source = "yfinance"

    # Freshness telemetry: how stale is the latest bar per symbol?
    # Bar timestamps are stored as naive IST strings; compare against
    # naive IST now so the delta is the actual lag in seconds.
    if data:
        try:
            now_naive_ist = datetime.now(_ist).replace(tzinfo=None)
            ages = {}
            for disp, cs in data.items():
                last_ts = datetime.strptime(cs[-1]["date"], "%Y-%m-%dT%H:%M:%S")
                ages[disp] = round((now_naive_ist - last_ts).total_seconds(), 1)
            log.info("[FVG-Tap fetch] source=%s latest_bar_age_sec=%s",
                     source, ages)
        except Exception:
            pass

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
            log.info("fvg_tap published rec #%s + Telegram: %s %s @ %s",
                     rec_id, sym, sig["direction"], sig["entry"])
        else:
            # 2026-06-01 publish gap: a fire reached the ledger but no rec/
            # Telegram surfaced. Make the reason explicit instead of silent.
            log.warning(
                "fvg_tap publish: create_stock_recommendation returned %r for "
                "%s %s (entry=%s sl=%s t=%s cmp=%s) — NO rec/Telegram. Likely "
                "rejected by a recommendation gate; investigate.",
                rec_id, sym, sig["direction"], sig["entry"], sig["sl"],
                sig["target"], sig["entry"])
        return rec_id
    except Exception as exc:
        log.warning("fvg_tap publish FAILED (%s): %s", sym, exc, exc_info=True)
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
                # 2026-06-01 fix (detection-timing gap): the detector only
                # returns a signal when the engulfing confirmation is the LAST
                # closed bar. With the */5 cron + Kite's bar-close lag the tick
                # rarely lands on that exact bar, so most setups were missed
                # (1 of 4 caught on 2026-06-01). Re-run the SAME frozen detector
                # on the last N closed bars as decision points so a setup
                # confirmed a tick ago still fires. Dedup by confirm_time keeps
                # it single-shot. The backtest rule itself is unchanged.
                sig = None
                for _back in range(_LIVE_LOOKBACK):
                    sub = candles if _back == 0 else candles[:-_back]
                    if len(sub) < 80:
                        continue
                    cand = evaluate_fvg_tap(sub, direction)
                    if cand and f"{sym}|{direction}|{cand.get('confirm_time')}" not in _SEEN:
                        sig = cand
                        break
                if not sig:
                    continue
                key = f"{sym}|{direction}|{sig.get('confirm_time')}"
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
