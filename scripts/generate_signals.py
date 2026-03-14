"""
scripts/generate_signals.py — Signal generation runner.

Scans all symbols with registered strategies and outputs
actionable trade signals to signals/output/.

Usage:
    python scripts/generate_signals.py
    python scripts/generate_signals.py --symbols "NSE:NIFTY 50,NSE:NIFTY BANK"
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger("signal_generator")

OUTPUT_DIR = Path(__file__).parent.parent / "signals" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate Trading Signals")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols")
    parser.add_argument("--min-confidence", type=float, default=5.0)
    parser.add_argument("--max-signals", type=int, default=5)
    return parser.parse_args()


def run_signal_scan(args):
    from signals.pipeline import SignalPipeline

    pipeline = SignalPipeline(
        max_signals_per_scan=args.max_signals,
        min_confidence=args.min_confidence,
    )

    logger.info("=" * 50)
    logger.info("SIGNAL SCAN — %s", datetime.now().isoformat())
    logger.info("Min Confidence: %.1f | Max Signals: %d",
                args.min_confidence, args.max_signals)
    logger.info("=" * 50)

    # Load symbol data (extend this with your data source)
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    else:
        symbols = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]

    symbol_data = {}
    for sym in symbols:
        try:
            from data.ingestion import DataIngestion
            ingestion = DataIngestion(source="yfinance")
            df = ingestion.fetch_historical(sym, interval="5minute", days=5)
            if not df.empty:
                candles = df.to_dict("records")
                symbol_data[sym] = {"ltf": candles, "htf": candles}
                logger.info("Loaded %d candles for %s", len(candles), sym)
        except Exception:
            logger.exception("Failed to load data for %s", sym)

    if not symbol_data:
        logger.warning("No symbol data loaded. Exiting.")
        return

    signals = pipeline.scan(symbol_data)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"signals_{timestamp}.json"

    output = {
        "scan_time": datetime.now().isoformat(),
        "signals": [s.to_dict() for s in signals],
        "rejected_count": len(pipeline.rejected),
        "rejected_summary": pipeline.rejected[:20],
    }

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    logger.info("Generated %d signals → %s", len(signals), output_file)

    for s in signals:
        logger.info("  SIGNAL: %s %s %s | Entry=%.2f SL=%.2f TP=%.2f | RR=%.1f Conf=%.1f",
                     s.setup, s.symbol, s.direction, s.entry, s.stop_loss, s.target,
                     s.rr, s.confidence)


if __name__ == "__main__":
    args = parse_args()
    run_signal_scan(args)
