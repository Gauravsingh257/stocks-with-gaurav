"""
Feature 6: Pre-Market Chart Scan via TradingView

⚠️ PARKED — Not solving core problem right now.
Keep for future use but don't add to daily workflow yet.

Usage:
    python scripts/premarket_chart_scan.py
    python scripts/premarket_chart_scan.py --symbols "NSE:NIFTY,NSE:BANKNIFTY" --send-telegram

Designed to run at ~08:45 IST before market open.
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.tv_mcp_bridge import (
    is_connected,
    set_symbol,
    set_timeframe,
    capture_screenshot,
    read_pine_levels,
    read_chart_state,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("premarket_scan")

DEFAULT_SYMBOLS = ["NSE:NIFTY", "NSE:BANKNIFTY"]
TIMEFRAMES = ["D", "240"]  # Daily and 4H
OUT_DIR = Path("premarket_scans")


def scan_symbol(symbol: str) -> dict:
    """Scan a single symbol across timeframes, capture charts + Pine levels."""
    result = {"symbol": symbol, "timeframes": {}}
    
    set_symbol(symbol)
    time.sleep(1.5)
    
    for tf in TIMEFRAMES:
        tf_label = "Daily" if tf == "D" else f"{tf}m"
        set_timeframe(tf)
        time.sleep(2)  # Let chart load
        
        # Capture screenshot
        date_str = datetime.now().strftime("%Y%m%d")
        sym_safe = symbol.replace(":", "_")
        ss_path = str(OUT_DIR / f"{date_str}_{sym_safe}_{tf}.png")
        
        try:
            capture_screenshot(ss_path)
        except Exception as e:
            log.warning(f"Screenshot failed for {symbol} {tf_label}: {e}")
            ss_path = None
        
        # Read Pine indicator levels
        pine_levels = None
        try:
            pine_levels = read_pine_levels("SMC")
        except Exception:
            pass
        
        # Read chart state (indicators, etc.)
        chart_state = None
        try:
            chart_state = read_chart_state()
        except Exception:
            pass
        
        result["timeframes"][tf_label] = {
            "screenshot": ss_path,
            "pine_levels": pine_levels,
            "chart_state": chart_state,
        }
        
        log.info(f"  {symbol} {tf_label}: {'✅' if ss_path else '❌'} screenshot, "
                 f"{'✅' if pine_levels else '❌'} pine levels")
    
    return result


def determine_bias(scan_data: dict) -> str:
    """Simple bias determination from Pine levels."""
    pine_daily = (scan_data.get("timeframes", {})
                  .get("Daily", {})
                  .get("pine_levels"))
    
    if not pine_daily:
        return "NEUTRAL (no Pine data)"
    
    # Look for bias indicators in Pine levels
    levels = pine_daily if isinstance(pine_daily, list) else []
    
    # Simple heuristic: if more levels are below current price => bullish structure
    # This is a placeholder — real logic depends on your Pine indicator output
    return "See chart for structure analysis"


def format_telegram_message(results: list[dict]) -> str:
    """Format scan results as a Telegram message."""
    date_str = datetime.now().strftime("%d %b %Y")
    
    msg = f"🌅 <b>Pre-Market Chart Scan — {date_str}</b>\n\n"
    
    for r in results:
        symbol = r["symbol"]
        bias = determine_bias(r)
        msg += f"<b>{symbol}</b>\n"
        
        for tf_label, tf_data in r.get("timeframes", {}).items():
            pine = tf_data.get("pine_levels")
            level_count = len(pine) if pine and isinstance(pine, list) else 0
            msg += f"  {tf_label}: {level_count} key levels"
            if tf_data.get("screenshot"):
                msg += " 📸"
            msg += "\n"
        
        msg += f"  Bias: {bias}\n\n"
    
    msg += "📊 <i>Charts captured — review in trade_screenshots/</i>"
    return msg


def send_to_telegram(message: str, screenshots: list[str]):
    """Send scan results to Telegram."""
    try:
        from services.telegram_bot import telegram_send_signal, telegram_send_image
        
        telegram_send_signal(message, signal_id=f"premarket_{datetime.now().strftime('%Y%m%d')}")
        
        for ss in screenshots[:4]:  # Max 4 screenshots
            if ss and Path(ss).exists():
                telegram_send_image(ss, caption=Path(ss).stem)
        
        log.info("Telegram messages sent")
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def run_scan(symbols: list[str] | None = None, send_telegram: bool = False):
    """Main scan function."""
    if not is_connected():
        log.error("TradingView not connected. Ensure it's running with CDP on port 9222.")
        return
    
    symbols = symbols or DEFAULT_SYMBOLS
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    log.info(f"═══ Pre-Market Chart Scan: {', '.join(symbols)} ═══")
    
    results = []
    all_screenshots = []
    
    for sym in symbols:
        log.info(f"Scanning {sym}...")
        scan = scan_symbol(sym)
        results.append(scan)
        
        for tf_data in scan.get("timeframes", {}).values():
            if tf_data.get("screenshot"):
                all_screenshots.append(tf_data["screenshot"])
    
    # Save JSON report
    date_str = datetime.now().strftime("%Y%m%d")
    report_path = OUT_DIR / f"scan_{date_str}.json"
    report_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    log.info(f"Report saved: {report_path}")
    
    # Format and optionally send Telegram
    tg_msg = format_telegram_message(results)
    log.info(f"\n{tg_msg}\n")
    
    if send_telegram:
        send_to_telegram(tg_msg, all_screenshots)
    
    log.info(f"═══ Scan complete: {len(results)} symbols, {len(all_screenshots)} screenshots ═══")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-Market TradingView Chart Scanner")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                        help="Comma-separated TradingView symbols")
    parser.add_argument("--send-telegram", action="store_true", help="Send results to Telegram")
    
    args = parser.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",")]
    
    run_scan(symbols=symbols, send_telegram=args.send_telegram)
