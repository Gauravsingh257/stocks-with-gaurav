"""
Feature 3: Replay-Based Debugging System

Step through missed trades candle-by-candle using TradingView's replay mode.
Captures screenshots at each step for visual analysis.

Usage:
    python scripts/replay_debug.py --symbol "NSE:NIFTY" --date 2026-04-06 --timeframe 5
    python scripts/replay_debug.py --symbol "NSE:BANKNIFTY" --date 2026-04-06 --start 09:15 --end 10:30
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tv_mcp_bridge import (
    is_connected,
    set_symbol,
    set_timeframe,
    capture_screenshot,
    read_pine_levels,
    read_chart_state,
    _cdp_send,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("replay_debug")

# ── TradingView Replay MCP commands ──
# These call the MCP server tools via CDP. We wrap them as simple functions.

MCP_BASE = "http://localhost:9222"


def _mcp_call(tool_name: str, args: dict | None = None):
    """Call a TradingView MCP tool via the CDP bridge.
    
    This uses the same CDP connection as tv_mcp_bridge but sends
    TV keyboard shortcuts / API calls that the MCP server would use.
    """
    # For replay, we use the TradingView internal API via CDP evaluate
    return _cdp_send("Runtime.evaluate", {
        "expression": f"window.__TV_MCP__ && window.__TV_MCP__.call('{tool_name}', {json.dumps(args or {})})",
        "returnByValue": True,
    })


def replay_start(date_str: str):
    """Start TradingView replay mode at a given date."""
    result = _cdp_send("Runtime.evaluate", {
        "expression": f"""
            (function() {{
                // Open replay via keyboard shortcut or UI
                var evt = new KeyboardEvent('keydown', {{key: 'r', altKey: true}});
                document.dispatchEvent(evt);
                return 'replay_start_triggered';
            }})()
        """,
        "returnByValue": True,
    })
    log.info(f"Replay start triggered for {date_str}")
    return result


def replay_step():
    """Advance replay by one candle."""
    result = _cdp_send("Runtime.evaluate", {
        "expression": """
            (function() {
                // Forward one bar in replay
                var evt = new KeyboardEvent('keydown', {key: 'ArrowRight'});
                document.dispatchEvent(evt);
                return 'step_forward';
            })()
        """,
        "returnByValue": True,
    })
    return result


def replay_stop():
    """Stop replay mode."""
    result = _cdp_send("Runtime.evaluate", {
        "expression": """
            (function() {
                var evt = new KeyboardEvent('keydown', {key: 'Escape'});
                document.dispatchEvent(evt);
                return 'replay_stopped';
            })()
        """,
        "returnByValue": True,
    })
    log.info("Replay stopped")
    return result


def run_replay_debug(symbol: str, date: str, timeframe: str = "5",
                     start_time: str = "09:15", end_time: str = "15:30",
                     step_delay: float = 1.5):
    """
    Main replay debug loop.
    
    1. Sets symbol + timeframe on chart
    2. Scrolls to the target date
    3. Steps candle by candle, capturing screenshots + Pine levels at each step
    4. Saves a JSON report with all collected data
    """
    # Validate connection
    if not is_connected():
        log.error("TradingView not connected. Ensure it's running with CDP on port 9222.")
        return

    # Output directory
    out_dir = Path(f"replay_debug/{symbol.replace(':', '_')}_{date}")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    log.info(f"═══ Replay Debug: {symbol} on {date} ({timeframe}m) ═══")
    log.info(f"Window: {start_time} → {end_time}")
    log.info(f"Output: {out_dir}")

    # Step 1: Set chart to target symbol + timeframe
    set_symbol(symbol)
    time.sleep(1)
    set_timeframe(timeframe)
    time.sleep(1)

    # Step 2: Scroll to date
    scroll_date = f"{date}T{start_time}:00"
    _cdp_send("Runtime.evaluate", {
        "expression": f"""
            (function() {{
                // Use TV's goToDate if available
                if (window.TradingView && window.TradingView.activeChart) {{
                    window.TradingView.activeChart().setVisibleRange({{
                        from: new Date('{scroll_date}').getTime() / 1000,
                        to: new Date('{date}T{end_time}:00').getTime() / 1000
                    }});
                }}
                return 'scrolled';
            }})()
        """,
        "returnByValue": True,
    })
    time.sleep(2)

    # Step 3: Start replay
    replay_start(scroll_date)
    time.sleep(2)

    # Step 4: Step through candles
    steps = []
    tf_minutes = int(timeframe)
    start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M")
    total_candles = int((end_dt - start_dt).total_seconds() / (tf_minutes * 60))
    
    log.info(f"Stepping through ~{total_candles} candles...")

    for i in range(total_candles):
        candle_time = start_dt + timedelta(minutes=i * tf_minutes)
        candle_str = candle_time.strftime("%H:%M")
        
        # Advance one candle
        replay_step()
        time.sleep(step_delay)

        # Capture screenshot
        ss_path = str(out_dir / f"step_{i:03d}_{candle_str.replace(':', '')}.png")
        try:
            capture_screenshot(ss_path)
        except Exception as e:
            log.warning(f"Screenshot failed at step {i}: {e}")
            ss_path = None

        # Read Pine indicator levels
        pine_levels = None
        try:
            pine_levels = read_pine_levels("SMC")
        except Exception:
            pass

        step_data = {
            "step": i,
            "candle_time": candle_str,
            "screenshot": ss_path,
            "pine_levels": pine_levels,
        }
        steps.append(step_data)
        
        if i % 10 == 0:
            log.info(f"  Step {i}/{total_candles} — {candle_str}")

    # Step 5: Stop replay
    replay_stop()

    # Step 6: Save report
    report = {
        "symbol": symbol,
        "date": date,
        "timeframe": f"{timeframe}m",
        "window": f"{start_time}-{end_time}",
        "total_steps": len(steps),
        "steps": steps,
        "generated_at": datetime.now().isoformat(),
    }
    
    report_path = out_dir / "replay_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    
    log.info(f"═══ Replay complete: {len(steps)} steps saved to {report_path} ═══")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TradingView Replay Debugger")
    parser.add_argument("--symbol", required=True, help="TradingView symbol (e.g. NSE:NIFTY)")
    parser.add_argument("--date", required=True, help="Date to replay (YYYY-MM-DD)")
    parser.add_argument("--timeframe", default="5", help="Timeframe in minutes (default: 5)")
    parser.add_argument("--start", default="09:15", help="Start time (default: 09:15)")
    parser.add_argument("--end", default="15:30", help="End time (default: 15:30)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between steps in seconds")
    
    args = parser.parse_args()
    
    run_replay_debug(
        symbol=args.symbol,
        date=args.date,
        timeframe=args.timeframe,
        start_time=args.start,
        end_time=args.end,
        step_delay=args.delay,
    )
