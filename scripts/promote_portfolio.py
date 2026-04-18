"""Auto-promote and verify portfolio."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.backend.db import init_db
from dashboard.backend.db.portfolio import init_portfolio_db

init_db()   # ensures all tables + migrations
init_portfolio_db()

from services.idea_selector import select_and_promote
from dashboard.backend.db.portfolio import get_portfolio, get_portfolio_counts

sw = select_and_promote("SWING")
lt = select_and_promote("LONGTERM")
print(f"Promoted: swing={sw}, longterm={lt}")

counts = get_portfolio_counts()
print(f"Counts: {counts}")

for h in ("SWING", "LONGTERM"):
    pos = get_portfolio(h)
    print(f"\n{h}: {len(pos)} positions")
    for p in pos:
        print(f"  {p['symbol']}: entry={p['entry_price']}, sl={p['stop_loss']}, status={p['status']}, pnl={p.get('profit_loss_pct',0)}%")
