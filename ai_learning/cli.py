"""
CLI Entry Point — Command-line interface for the AI Learning System.
=====================================================================
"""

import argparse
import logging
import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai_learning.pipeline import TradingAIPipeline
from ai_learning.data.schemas import ManualTrade


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_ingest(args):
    """Ingest trades from file."""
    pipeline = TradingAIPipeline()

    if args.source == "csv":
        count = pipeline.ingest_trades_csv(args.file)
    elif args.source == "json":
        count = pipeline.ingest_trades_json(args.file)
    elif args.source == "ledger":
        count = pipeline.ingest_from_ledger()
    else:
        print(f"Unknown source: {args.source}")
        return

    print(f"✅ Ingested {count} trades")
    status = pipeline.status()
    print(f"   Total in DB: {status['trades_ingested']}")


def cmd_extract(args):
    """Extract SMC features."""
    pipeline = TradingAIPipeline()

    if args.live:
        count = pipeline.extract_features(live=True)
    else:
        print("Provide --live flag or candle data for feature extraction")
        return

    print(f"✅ Extracted features for {count} trades")


def cmd_learn(args):
    """Run PRISM (Agent 1): Learn trading style."""
    pipeline = TradingAIPipeline()

    coverage = pipeline.agent1.get_feature_coverage()
    print(f"Trade coverage: {coverage}")

    profile = pipeline.agent1.learn()
    print(profile.summary())


def cmd_generate(args):
    """Run FORGE (Agent 2): Generate strategies."""
    pipeline = TradingAIPipeline()

    profile = pipeline.agent1.get_profile()
    if not profile:
        print("❌ No profile found. Run 'learn' first.")
        return

    rules = pipeline.agent2.generate(profile)
    pipeline.agent2.export_strategy_module(rules)
    pipeline.agent2.export_engine_integration(rules)
    pipeline.agent2.export_rules_json(rules)

    print(f"✅ Generated {len(rules)} strategy rules")
    for r in rules:
        print(f"   • {r.strategy_name}: {len(r.conditions)} conditions, "
              f"confidence={r.confidence:.1%}")


def cmd_pipeline(args):
    """Run full pipeline."""
    pipeline = TradingAIPipeline()

    # Check for trades
    status = pipeline.status()
    if status["trades_ingested"] == 0:
        if args.ingest_ledger:
            pipeline.ingest_from_ledger()
        else:
            print("❌ No trades in DB. Use 'ingest' command first or --ingest-ledger flag.")
            return

    report = pipeline.run_full_pipeline()
    print(json.dumps(report, indent=2, default=str))


def cmd_status(args):
    """Show pipeline status."""
    pipeline = TradingAIPipeline()
    status = pipeline.status()
    print(json.dumps(status, indent=2))


def cmd_scan(args):
    """Run one-time scan."""
    pipeline = TradingAIPipeline()

    profile = pipeline.agent1.get_profile()
    rules = pipeline.agent2.get_rules()

    if not profile or not rules:
        print("❌ No profile/rules. Run 'pipeline' first.")
        return

    # Load symbols
    symbols_file = Path(__file__).resolve().parent.parent / "stock_universe_fno.json"
    if symbols_file.exists():
        with open(symbols_file) as f:
            symbols_data = json.load(f)
            symbols = [f"NSE:{s}" for s in symbols_data
                       if isinstance(s, str)][:10]  # limit to 10 for safety
    else:
        symbols = ["NSE:NIFTY 50", "NSE:NIFTY BANK"]

    # Import fetch function
    try:
        from smc_mtf_engine_v4 import fetch_ohlc

        def fetch(symbol, interval):
            return fetch_ohlc(symbol, interval, lookback=200)

        signals = pipeline.scan_once(symbols, fetch)
        for sig in signals:
            print(sig.alert_text())
            print()
        if not signals:
            print("No signals detected")
    except ImportError:
        print("❌ Cannot import fetch_ohlc. Kite session required.")


def main():
    parser = argparse.ArgumentParser(
        description="AI Trading Style Learning System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m ai_learning.cli ingest --source csv --file my_trades.csv
  python -m ai_learning.cli ingest --source ledger
  python -m ai_learning.cli extract --live
  python -m ai_learning.cli learn
  python -m ai_learning.cli generate
  python -m ai_learning.cli pipeline --ingest-ledger
  python -m ai_learning.cli status
  python -m ai_learning.cli scan
        """,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Ingest
    p_ingest = subparsers.add_parser("ingest", help="Ingest trades")
    p_ingest.add_argument("--source", choices=["csv", "json", "ledger"],
                          required=True, help="Source type")
    p_ingest.add_argument("--file", help="Path to trade file")

    # Extract
    p_extract = subparsers.add_parser("extract", help="Extract features")
    p_extract.add_argument("--live", action="store_true",
                           help="Fetch candles live from Kite")

    # Learn
    subparsers.add_parser("learn", help="PRISM (Agent 1): Learn trading style")

    # Generate
    subparsers.add_parser("generate", help="FORGE (Agent 2): Generate strategies")

    # Pipeline
    p_pipeline = subparsers.add_parser("pipeline", help="Run full pipeline")
    p_pipeline.add_argument("--ingest-ledger", action="store_true",
                            help="Auto-ingest from trade_ledger_2026.csv")

    # Status
    subparsers.add_parser("status", help="Show pipeline status")

    # Scan
    subparsers.add_parser("scan", help="Run one-time live scan")

    args = parser.parse_args()
    setup_logging(args.verbose)

    commands = {
        "ingest": cmd_ingest,
        "extract": cmd_extract,
        "learn": cmd_learn,
        "generate": cmd_generate,
        "pipeline": cmd_pipeline,
        "status": cmd_status,
        "scan": cmd_scan,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
