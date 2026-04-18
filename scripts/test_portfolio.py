"""Quick portfolio validation script."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.backend.db.portfolio import (
    init_portfolio_db, add_position, get_portfolio, get_portfolio_counts,
    close_position, get_journal, get_journal_stats, seed_portfolio_from_recommendations,
)

def main():
    print("=== Portfolio System Validation ===")

    # 1. Init tables
    init_portfolio_db()
    print("[OK] Tables initialized")

    # 2. Seed from existing running trades
    seeded = seed_portfolio_from_recommendations()
    print(f"[OK] Seeded {seeded} positions from existing running_trades")

    # 3. Check counts
    counts = get_portfolio_counts()
    print(f"[OK] Portfolio counts: swing={counts['swing']}/{counts['swing_max']}, longterm={counts['longterm']}/{counts['longterm_max']}")

    # 4. List positions
    for horizon in ("SWING", "LONGTERM"):
        positions = get_portfolio(horizon)
        print(f"\n--- {horizon} Portfolio ({len(positions)} active) ---")
        for p in positions:
            sym = p["symbol"]
            entry = p["entry_price"]
            sl = p["stop_loss"]
            t1 = p.get("target_1") or "-"
            status = p["status"]
            pnl = p.get("profit_loss_pct", 0)
            days = p.get("days_held", 0)
            print(f"  {sym}: entry={entry} sl={sl} t1={t1} status={status} pnl={pnl}% days={days}")

    # 5. Check journal
    journal = get_journal()
    stats = get_journal_stats()
    print(f"\n--- Journal ---")
    print(f"Total closed trades: {stats['total_trades']}")
    print(f"Win rate: {stats['hit_rate_pct']}%")
    print(f"Avg P&L: {stats['avg_pnl_pct']}%")

    print("\n=== Validation Complete ===")

if __name__ == "__main__":
    main()
