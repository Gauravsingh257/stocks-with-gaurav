"""
Feature 5: Simple Content Pipeline — Instagram Only

Takes a closed trade + optional TradingView screenshot and generates
one Instagram carousel breakdown (3 slides).

Usage:
    python scripts/trade_to_content.py --trade-file trade_ledger_2026.csv --last 1
    python scripts/trade_to_content.py --trade-json '{"symbol":"NIFTY 50","entry":24100,...}'
"""

import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("trade_content")

OUT_DIR = Path("content_output")


def load_last_trades(csv_path: str = "trade_ledger_2026.csv", count: int = 1) -> list[dict]:
    """Load last N trades from the trade ledger CSV."""
    path = Path(csv_path)
    if not path.exists():
        log.error(f"Trade ledger not found: {csv_path}")
        return []
    
    with open(path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    
    return reader[-count:] if reader else []


def generate_instagram_content(trade: dict, screenshot_path: str | None = None) -> str:
    """Generate Instagram carousel text from a trade."""
    symbol = trade.get("symbol", "UNKNOWN")
    setup = trade.get("setup", trade.get("strategy", "SMC"))
    direction = trade.get("direction", "LONG")
    entry = trade.get("entry", trade.get("entry_price", "?"))
    sl = trade.get("sl", trade.get("stop_loss", "?"))
    target = trade.get("target", trade.get("tp1", "?"))
    result = trade.get("result", "?")
    exit_r = trade.get("exit_r", trade.get("pnl_r", "?"))
    exit_price = trade.get("exit_price", "?")
    date = trade.get("date", trade.get("entry_time", datetime.now().strftime("%Y-%m-%d")))
    
    is_win = str(result).upper() in ("WIN", "TARGET", "TRAIL WIN")
    emoji = "🟢" if is_win else "🔴"
    result_text = f"+{exit_r}R" if is_win else f"{exit_r}R"

    # Slide 1: The Setup
    slide1 = f"""📊 SLIDE 1 — THE SETUP
━━━━━━━━━━━━━━━━━━
{emoji} {symbol} | {direction} | {setup}
Date: {date}

Entry: {entry}
Stop Loss: {sl}
Target: {target}

💡 Why this trade?
Smart Money Concepts — Order Block + Break of Structure
{"📸 Use TradingView screenshot as background" if screenshot_path else "📸 Use chart screenshot as background"}
"""

    # Slide 2: The Result
    slide2 = f"""📊 SLIDE 2 — THE RESULT
━━━━━━━━━━━━━━━━━━
{emoji} {result_text}

Entry: {entry}
Exit: {exit_price}
Result: {result}

{"✅ Clean execution, target hit" if is_win else "❌ SL hit — risk managed"}
Risk: 1R = controlled
{"📸 Use exit chart screenshot" if screenshot_path else ""}
"""

    # Slide 3: The Lesson
    slide3 = f"""📊 SLIDE 3 — THE TAKEAWAY
━━━━━━━━━━━━━━━━━━
{"✅ Patience pays. Wait for the setup, execute the plan." if is_win else "❌ Not every setup works. The edge is in consistency."}

📈 Follow @stockswithgaurav for daily SMC setups
#SmartMoney #Trading #NIFTY #StocksWithGaurav #SMC
"""

    return f"{slide1}\n{slide2}\n{slide3}"


def generate_telegram_post(trade: dict, screenshot_path: str | None = None) -> str:
    """Generate a Telegram-ready post from a trade."""
    symbol = trade.get("symbol", "UNKNOWN")
    setup = trade.get("setup", trade.get("strategy", "SMC"))
    direction = trade.get("direction", "LONG")
    entry = trade.get("entry", trade.get("entry_price", "?"))
    sl = trade.get("sl", trade.get("stop_loss", "?"))
    target = trade.get("target", trade.get("tp1", "?"))
    result = trade.get("result", "?")
    exit_r = trade.get("exit_r", trade.get("pnl_r", "?"))
    exit_price = trade.get("exit_price", "?")
    
    is_win = str(result).upper() in ("WIN", "TARGET", "TRAIL WIN")
    emoji = "🟢" if is_win else "🔴"

    post = f"""{emoji} <b>Trade Update — {symbol}</b>

<b>Setup:</b> {setup} | {direction}
<b>Entry:</b> {entry}
<b>SL:</b> {sl}
<b>Target:</b> {target}

<b>Result:</b> {result} ({'+' if is_win else ''}{exit_r}R)
<b>Exit:</b> {exit_price}

{"✅ Clean setup, clean execution." if is_win else "❌ Part of the process. Risk was defined."}

📊 <i>Smart Money Concepts by @StocksWithGaurav</i>"""

    return post


def process_trade(trade: dict, screenshot_path: str | None = None):
    """Generate Instagram content for a single trade and save to file."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    
    symbol_safe = trade.get("symbol", "UNKNOWN").replace(" ", "_").replace(":", "_")
    date = trade.get("date", trade.get("entry_time", datetime.now().strftime("%Y%m%d")))
    date_safe = str(date).replace("-", "").replace(":", "").replace(" ", "_")[:8]
    prefix = f"{symbol_safe}_{date_safe}"

    # Check for TradingView screenshot
    if not screenshot_path:
        # Look in trade_screenshots/ directory
        ss_dir = Path("trade_screenshots")
        if ss_dir.exists():
            candidates = sorted(ss_dir.glob(f"*{symbol_safe}*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                screenshot_path = str(candidates[0])
                log.info(f"Found screenshot: {screenshot_path}")

    # Generate Instagram content only (STEP 4: single format)
    ig_content = generate_instagram_content(trade, screenshot_path)

    ig_path = OUT_DIR / f"{prefix}_instagram.txt"
    ig_path.write_text(ig_content, encoding="utf-8")
    
    log.info(f"Instagram content: {ig_path}")
    
    # Save JSON for potential API use
    combined = {
        "trade": trade,
        "screenshot": screenshot_path,
        "instagram": ig_content,
        "generated_at": datetime.now().isoformat(),
    }
    json_path = OUT_DIR / f"{prefix}_content.json"
    json_path.write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")
    
    return combined


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade → Content Generator")
    parser.add_argument("--trade-file", default="trade_ledger_2026.csv", help="Path to trade ledger CSV")
    parser.add_argument("--last", type=int, default=1, help="Process last N trades")
    parser.add_argument("--trade-json", help="Single trade as JSON string")
    parser.add_argument("--screenshot", help="Path to screenshot image")
    
    args = parser.parse_args()
    
    if args.trade_json:
        trades = [json.loads(args.trade_json)]
    else:
        trades = load_last_trades(args.trade_file, args.last)
    
    if not trades:
        log.error("No trades found.")
        sys.exit(1)
    
    for t in trades:
        log.info(f"═══ Generating content for {t.get('symbol', '?')} ═══")
        process_trade(t, args.screenshot)
    
    log.info(f"Done. {len(trades)} trade(s) processed → {OUT_DIR}/")
