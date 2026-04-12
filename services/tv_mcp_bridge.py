"""
TradingView MCP Bridge — lightweight CDP interface for the engine.

This module talks directly to TradingView Desktop via Chrome DevTools Protocol
(port 9222). It does NOT depend on the MCP server process — it uses the same
underlying CDP connection that the MCP tools use.

Used by:
    - Visual signal validation (capture chart on signal fire)
    - Trade close screenshots (annotated journal charts)
    - Pine cross-validation (read Pine indicator levels)
    - Pre-market automation (morning chart captures)

All functions are non-blocking, fire-and-forget with error swallowing.
A failed screenshot never blocks a trade.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

CDP_PORT = 9222
CDP_BASE = f"http://localhost:{CDP_PORT}"
SCREENSHOT_DIR = Path("trade_screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("tv_mcp_bridge")

# ─── CDP HELPERS ─────────────────────────────────────────────────────────────

def _cdp_get(path: str, timeout: float = 3.0) -> Optional[dict]:
    """GET request to CDP endpoint."""
    try:
        req = urllib.request.Request(f"{CDP_BASE}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _get_ws_url() -> Optional[str]:
    """Get the WebSocket debugger URL for the active TradingView page."""
    targets = _cdp_get("/json")
    if not targets:
        return None
    for t in targets:
        if "tradingview.com/chart" in t.get("url", ""):
            return t.get("webSocketDebuggerUrl")
    # Fallback: first page target
    for t in targets:
        if t.get("type") == "page":
            return t.get("webSocketDebuggerUrl")
    return None


def is_connected() -> bool:
    """Check if TradingView CDP is reachable."""
    info = _cdp_get("/json/version", timeout=2.0)
    return info is not None and "Browser" in (info or {})


def _cdp_send(ws_url: str, method: str, params: dict = None, timeout: float = 10.0) -> Optional[dict]:
    """Send a CDP command via WebSocket and wait for result.
    
    Uses a simple synchronous websocket approach to avoid adding dependencies.
    Falls back gracefully if websocket is unavailable.
    """
    try:
        import websocket  # pip install websocket-client (already in requirements)
        ws = websocket.create_connection(ws_url, timeout=timeout)
        msg_id = int(time.time() * 1000) % 1_000_000
        payload = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        ws.send(json.dumps(payload))

        # Wait for matching response
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                ws.close()
                return data.get("result")
        ws.close()
    except ImportError:
        logger.debug("websocket-client not installed — CDP commands unavailable")
    except Exception as e:
        logger.debug(f"CDP send failed: {e}")
    return None


# ─── CHART CONTROL ───────────────────────────────────────────────────────────

def set_symbol(symbol: str) -> bool:
    """Change the chart symbol. Maps engine symbols to TradingView format."""
    tv_symbol = _engine_to_tv_symbol(symbol)
    ws_url = _get_ws_url()
    if not ws_url:
        return False
    js = f'window.ChartApiInstance && window.ChartApiInstance.setSymbol("{tv_symbol}")'
    result = _cdp_send(ws_url, "Runtime.evaluate", {"expression": js})
    return result is not None


def set_timeframe(tf: str) -> bool:
    """Change the chart timeframe. tf: '1', '5', '15', '60', 'D', 'W'"""
    ws_url = _get_ws_url()
    if not ws_url:
        return False
    js = f'window.ChartApiInstance && window.ChartApiInstance.setResolution("{tf}")'
    result = _cdp_send(ws_url, "Runtime.evaluate", {"expression": js})
    return result is not None


def capture_screenshot(tag: str = "", symbol: str = "") -> Optional[str]:
    """Capture a screenshot of the current TradingView chart.
    
    Returns: file path to the saved PNG, or None on failure.
    """
    ws_url = _get_ws_url()
    if not ws_url:
        logger.debug("No TradingView page found for screenshot")
        return None

    try:
        result = _cdp_send(ws_url, "Page.captureScreenshot", {
            "format": "png",
            "quality": 80,
        })
        if not result or "data" not in result:
            return None

        import base64
        img_data = base64.b64decode(result["data"])

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        clean_sym = (symbol or "chart").replace(":", "_").replace(" ", "_")
        clean_tag = f"_{tag}" if tag else ""
        filename = f"{clean_sym}{clean_tag}_{ts}.png"
        filepath = SCREENSHOT_DIR / filename

        filepath.write_bytes(img_data)
        logger.info(f"📸 Screenshot saved: {filepath}")
        return str(filepath)

    except Exception as e:
        logger.debug(f"Screenshot capture failed: {e}")
        return None


# ─── PINE INDICATOR DATA ─────────────────────────────────────────────────────

def read_pine_levels(study_filter: str = "SMC") -> Optional[dict]:
    """Read line/box/label data from visible Pine indicators.
    
    Returns dict with keys: lines, boxes, labels (each a list).
    Only returns data from indicators whose name contains study_filter.
    """
    ws_url = _get_ws_url()
    if not ws_url:
        return None

    try:
        # Get all studies on the chart
        js_studies = """
        (function() {
            try {
                var chart = window.TradingViewApi._activeChartWidgetWV.value();
                var studies = chart.getAllStudies();
                return JSON.stringify(studies.map(s => ({id: s.id, name: s.name})));
            } catch(e) { return '[]'; }
        })()
        """
        result = _cdp_send(ws_url, "Runtime.evaluate", {
            "expression": js_studies,
            "returnByValue": True,
        })
        if not result:
            return None

        raw = result.get("result", {}).get("value", "[]")
        studies = json.loads(raw) if isinstance(raw, str) else raw

        # Filter to matching studies
        matching = [s for s in studies if study_filter.lower() in s.get("name", "").lower()]
        if not matching:
            return {"lines": [], "boxes": [], "labels": [], "matched_studies": []}

        # Read Pine drawing objects for matching studies
        entity_ids = [s["id"] for s in matching]
        all_lines = []
        all_boxes = []
        all_labels = []

        for eid in entity_ids:
            # Get Pine lines (horizontal levels)
            js_lines = f"""
            (function() {{
                try {{
                    var chart = window.TradingViewApi._activeChartWidgetWV.value();
                    var study = chart.getStudyById("{eid}");
                    if (!study) return '[]';
                    var shapes = study.getPineShapes ? study.getPineShapes() : [];
                    var lines = shapes.filter(s => s.type === 'line').map(s => ({{
                        price1: s.points?.[0]?.price,
                        price2: s.points?.[1]?.price,
                        color: s.properties?.linecolor
                    }}));
                    return JSON.stringify(lines);
                }} catch(e) {{ return '[]'; }}
            }})()
            """
            r = _cdp_send(ws_url, "Runtime.evaluate", {
                "expression": js_lines,
                "returnByValue": True,
            })
            if r:
                val = r.get("result", {}).get("value", "[]")
                lines = json.loads(val) if isinstance(val, str) else val
                all_lines.extend(lines)

        return {
            "lines": all_lines,
            "boxes": all_boxes,
            "labels": all_labels,
            "matched_studies": [s["name"] for s in matching],
        }

    except Exception as e:
        logger.debug(f"Pine level read failed: {e}")
        return None


def read_chart_state() -> Optional[dict]:
    """Get current chart symbol, timeframe, and study list."""
    ws_url = _get_ws_url()
    if not ws_url:
        return None

    js = """
    (function() {
        try {
            var chart = window.TradingViewApi._activeChartWidgetWV.value();
            return JSON.stringify({
                symbol: chart.symbol(),
                resolution: chart.resolution(),
                studies: chart.getAllStudies().map(s => ({id: s.id, name: s.name}))
            });
        } catch(e) { return '{}'; }
    })()
    """
    result = _cdp_send(ws_url, "Runtime.evaluate", {
        "expression": js,
        "returnByValue": True,
    })
    if not result:
        return None
    raw = result.get("result", {}).get("value", "{}")
    return json.loads(raw) if isinstance(raw, str) else raw


# ─── SYMBOL MAPPING ──────────────────────────────────────────────────────────

def _scroll_to_candle(timestamp) -> bool:
    """Scroll the TradingView chart to a specific candle time.
    
    STEP 3: Ensures screenshot captures the exact signal candle,
    not wherever the chart has drifted to.
    
    Args:
        timestamp: datetime, ISO string, or Unix seconds
    """
    ws_url = _get_ws_url()
    if not ws_url:
        return False
    
    try:
        from datetime import datetime as _dt
        if isinstance(timestamp, str):
            # Parse ISO format
            ts = _dt.fromisoformat(timestamp.replace("Z", "+00:00"))
            unix_ts = int(ts.timestamp())
        elif isinstance(timestamp, (int, float)):
            unix_ts = int(timestamp)
        elif hasattr(timestamp, "timestamp"):
            unix_ts = int(timestamp.timestamp())
        else:
            return False
        
        # Scroll chart so the target bar is centered
        js = f"""
        (function() {{
            try {{
                var chart = window.TradingViewApi._activeChartWidgetWV.value();
                chart.setVisibleRange({{
                    from: {unix_ts} - 3600,
                    to: {unix_ts} + 3600
                }});
                return 'scrolled';
            }} catch(e) {{ return 'error: ' + e.message; }}
        }})()
        """
        result = _cdp_send(ws_url, "Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
        })
        return result is not None
    except Exception as e:
        logger.debug(f"Scroll to candle failed: {e}")
        return False

def _engine_to_tv_symbol(engine_symbol: str) -> str:
    """Convert engine symbol names to TradingView format.
    
    Engine uses: 'NIFTY 50', 'NIFTY BANK', 'RELIANCE', etc.
    TradingView needs: 'NSE:NIFTY', 'NSE:BANKNIFTY', 'NSE:RELIANCE'
    """
    mapping = {
        "NIFTY 50": "NSE:NIFTY",
        "NIFTY BANK": "NSE:BANKNIFTY",
        "NIFTY50": "NSE:NIFTY",
        "BANKNIFTY": "NSE:BANKNIFTY",
        "FINNIFTY": "NSE:FINNIFTY",
    }
    upper = engine_symbol.strip().upper()
    if upper in mapping:
        return mapping[upper]
    # If no colon, assume NSE
    if ":" not in upper:
        return f"NSE:{upper}"
    return upper


# ─── HIGH-LEVEL ACTIONS (used by engine hooks) ──────────────────────────────

def capture_signal_chart(signal: dict) -> Optional[str]:
    """Capture a chart screenshot for a new signal.
    
    Sets the symbol and timeframe, then scrolls to the signal candle
    before capturing (STEP 3: exact candle timing).
    Returns file path or None.
    """
    if not is_connected():
        return None

    symbol = signal.get("symbol", "")
    set_symbol(symbol)
    # Use 5m timeframe for signal validation (LTF)
    set_timeframe("5")
    time.sleep(1.5)  # Wait for chart to load
    
    # STEP 3: Scroll to exact signal candle to avoid chart drift
    entry_time = signal.get("entry_time") or signal.get("choch_time")
    if entry_time:
        _scroll_to_candle(entry_time)
        time.sleep(0.5)
    
    return capture_screenshot(tag="signal", symbol=symbol)


def capture_trade_close_chart(trade: dict) -> Optional[str]:
    """Capture annotated chart when a trade closes.
    
    Returns file path or None.
    """
    if not is_connected():
        return None

    symbol = trade.get("symbol", "")
    set_symbol(symbol)
    set_timeframe("5")
    time.sleep(1.5)
    result = trade.get("result", "UNKNOWN")
    return capture_screenshot(tag=f"close_{result}", symbol=symbol)


def get_pine_cross_validation(signal: dict) -> Optional[dict]:
    """Compare engine-detected zones with Pine indicator zones.
    
    STEP 2: Validates OB, FVG, and BOS/CHOCH — not just OB.
    STEP 6: Returns confidence_adjustment (+1 for confirm, -1 for mismatch).
    
    Returns: {
        match_ob: bool, match_fvg: bool, match_bos: bool,
        confidence_adjustment: int (-1, 0, or +1),
        delta: float, engine_levels: {...}, pine_levels: {...}
    }
    """
    if not is_connected():
        return None

    symbol = signal.get("symbol", "")
    set_symbol(symbol)
    set_timeframe("5")
    time.sleep(1.0)

    pine_data = read_pine_levels("SMC")
    if not pine_data:
        return {"match": None, "reason": "no_pine_data", "confidence_adjustment": 0}

    # Extract Pine levels
    pine_lines = pine_data.get("lines", [])
    pine_labels = pine_data.get("labels", [])
    
    pine_prices = sorted(set(
        p for line in pine_lines
        for p in [line.get("price1"), line.get("price2")]
        if p is not None
    ))

    # Parse Pine labels for BOS/CHOCH markers
    pine_has_bos = any(
        "bos" in str(lbl.get("text", "")).lower()
        for lbl in pine_labels
    ) if pine_labels else None
    pine_has_choch = any(
        "choch" in str(lbl.get("text", "")).lower()
        for lbl in pine_labels
    ) if pine_labels else None

    engine_entry = signal.get("entry", 0)
    threshold = engine_entry * 0.003 if engine_entry else 50  # 0.3%

    # ── OB validation ──
    engine_ob = signal.get("ob")  # tuple (low, high) or None
    match_ob = None
    ob_delta = float("inf")
    if engine_ob and pine_prices:
        ob_low, ob_high = engine_ob
        pine_near_ob = [
            p for p in pine_prices
            if abs(p - ob_low) < threshold or abs(p - ob_high) < threshold
        ]
        match_ob = len(pine_near_ob) > 0
        ob_delta = min(
            min(abs(p - ob_low), abs(p - ob_high)) for p in pine_prices
        ) if pine_prices else float("inf")

    # ── FVG validation ──
    engine_fvg = signal.get("fvg")  # tuple (low, high) or None
    match_fvg = None
    if engine_fvg and pine_prices:
        fvg_low, fvg_high = engine_fvg[:2] if len(engine_fvg) >= 2 else (engine_fvg[0], engine_fvg[0])
        pine_near_fvg = [
            p for p in pine_prices
            if abs(p - fvg_low) < threshold or abs(p - fvg_high) < threshold
        ]
        match_fvg = len(pine_near_fvg) > 0

    # ── BOS/CHOCH validation ──
    engine_bos = bool(signal.get("bos_confirmed"))
    engine_choch = bool(signal.get("choch_time") or signal.get("choch_detected"))
    match_bos = None
    if pine_has_bos is not None and engine_bos:
        match_bos = pine_has_bos == engine_bos

    # ── STEP 6: Confidence adjustment ──
    # +1 if Pine confirms (OB match + at least one of FVG/BOS match)
    # -1 if Pine contradicts (OB explicitly mismatches)
    #  0 if inconclusive (no Pine data for comparison)
    confirms = 0
    contradicts = 0
    checked = 0
    
    if match_ob is True:
        confirms += 1; checked += 1
    elif match_ob is False:
        contradicts += 1; checked += 1
    
    if match_fvg is True:
        confirms += 1; checked += 1
    elif match_fvg is False:
        # FVG mismatch is less critical than OB mismatch
        checked += 1
    
    if match_bos is True:
        confirms += 1; checked += 1
    elif match_bos is False:
        contradicts += 1; checked += 1
    
    if checked == 0:
        confidence_adjustment = 0  # No data to compare
    elif confirms >= 2:
        confidence_adjustment = 1   # Strong Pine confirmation → boost score
    elif contradicts >= 1 and confirms == 0:
        confidence_adjustment = -1  # Pine contradiction → reduce score
    else:
        confidence_adjustment = 0   # Mixed / inconclusive

    return {
        "match_ob": match_ob,
        "match_fvg": match_fvg,
        "match_bos": match_bos,
        "pine_has_choch": pine_has_choch,
        "delta": round(ob_delta, 2) if ob_delta != float("inf") else None,
        "engine_ob": list(engine_ob) if engine_ob else None,
        "engine_fvg": list(engine_fvg) if engine_fvg else None,
        "engine_bos": engine_bos,
        "engine_choch": engine_choch,
        "pine_all_levels": pine_prices,
        "confidence_adjustment": confidence_adjustment,
        "confirms": confirms,
        "contradicts": contradicts,
    }
