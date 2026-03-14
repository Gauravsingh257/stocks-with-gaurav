from services.ranking_engine import run_weekly_rankings
from services.universe_manager import load_nse_universe


def test_universe_loader_returns_nse_symbols():
    universe = load_nse_universe(target_size=1800)
    assert universe.actual_size > 0
    assert all(symbol.startswith("NSE:") for symbol in universe.symbols)


def test_weekly_rankings_are_deterministic_top10():
    swing_a, long_a = run_weekly_rankings(top_k=10, target_universe=1800)
    swing_b, long_b = run_weekly_rankings(top_k=10, target_universe=1800)

    assert len(swing_a.ideas) <= 10
    assert len(long_a.ideas) <= 10
    assert [i.symbol for i in swing_a.ideas] == [i.symbol for i in swing_b.ideas]
    assert [i.symbol for i in long_a.ideas] == [i.symbol for i in long_b.ideas]
    if swing_a.ideas:
        assert swing_a.ideas[0].reasoning != ""
        assert len(swing_a.ideas[0].technical_signals) >= 3
