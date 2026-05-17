"""
tests/test_equity_state_machine_equivalence.py — PHASE G2-6 Step 1 CI gate
=========================================================================
Two properties the production ONLINE engine
(`services.equity_state_machine.scan_fires_online`) must satisfy. If
either fails the engine is unsafe to run live and G2-6 must not proceed.

A. SELF-CONSISTENCY (online determinism): evaluating day-by-day over
   growing prefixes (exactly how the live daily agent sees data), with a
   fire discovered once and never revised, yields the IDENTICAL fire set
   as one evaluation on the full array. No decision is provisional.

B. RULE FIDELITY: every fire the online engine emits also appears,
   byte-identical (entry/SL/target/formed/tapped), in the batch oracle
   `state_machine_sim.simulate_state_machine_entries`. The online engine
   never invents a trade the G2-5-validated rules would not; it correctly
   emits a SUBSET (it drops the oracle's batch-only phantom/overlap tail
   fires — the very divergence the first Step-1 test exposed).

Deterministic, seeded, offline — no network/DB/wall-clock. The candle
series are TEST FIXTURES for a structural property, not product data.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.equity_state_machine import scan_fires_online
from services.state_machine_sim import simulate_state_machine_entries
from services.research_levels import daily_candles_to_weekly

_MIN_HISTORY = 60


def _gen_daily(seed: int, n: int = 520) -> list[dict]:
    """Deterministic noisy upward random walk with periodic sharp
    pullbacks then gap-ups — naturally manufactures order blocks, FVGs,
    taps and hammer confirmations so the checks run on fire-producing
    inputs. Seeded ⇒ identical across CI runs (no flakiness)."""
    rng = random.Random(seed)
    px = 100.0
    out: list[dict] = []
    for i in range(n):
        shock = rng.uniform(-0.028, 0.034)
        if i % 37 == 0:
            shock -= 0.05
        if i % 37 == 3:
            shock += 0.07
        o = px
        c = max(1.0, px * (1.0 + 0.0025 + shock))
        hi = max(o, c) * (1.0 + abs(rng.uniform(0.0, 0.015)))
        lo = min(o, c) * (1.0 - abs(rng.uniform(0.0, 0.018)))
        out.append({
            "date": f"2024-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}",
            "open": round(o, 2), "high": round(hi, 2),
            "low": round(lo, 2), "close": round(c, 2),
            "volume": 100000 + rng.randint(0, 50000),
        })
        px = c
    return out


class TestEquityStateMachineEquivalence(unittest.TestCase):
    SEEDS = list(range(1, 13))

    def test_A_online_self_consistency(self):
        total = 0
        for seed in self.SEEDS:
            daily = _gen_daily(seed)
            weekly = daily_candles_to_weekly(daily)
            n = len(daily)

            discovered: dict[int, dict] = {}
            for d in range(_MIN_HISTORY, n):
                for fr in scan_fires_online(daily[: d + 1], weekly):
                    fd = fr.to_dict()
                    if fr.entry_idx in discovered:
                        # value stability: a fire never changes once seen
                        self.assertEqual(
                            discovered[fr.entry_idx], fd,
                            f"seed={seed}: fire@{fr.entry_idx} revised "
                            f"after discovery — decision was provisional",
                        )
                    else:
                        discovered[fr.entry_idx] = fd

            full = [f.to_dict() for f in scan_fires_online(daily, weekly)]
            accumulated = [discovered[k] for k in sorted(discovered)]
            self.assertEqual(
                accumulated, full,
                f"seed={seed}: day-by-day accumulation != full-array "
                f"evaluation — online engine is not self-consistent",
            )
            total += len(full)

        self.assertGreater(
            total, 0,
            "no fires across any seed — checks were vacuous; fixture no "
            "longer exercises the state machine",
        )

    def test_B_rule_fidelity_subset_of_oracle(self):
        any_strict_subset = False
        for seed in self.SEEDS:
            daily = _gen_daily(seed)
            weekly = daily_candles_to_weekly(daily)

            online = [f.to_dict() for f in scan_fires_online(daily, weekly)]
            oracle = [f.to_dict() for f in
                      simulate_state_machine_entries(daily, weekly)]
            oracle_set = {tuple(sorted(d.items())) for d in oracle}

            for f in online:
                self.assertIn(
                    tuple(sorted(f.items())), oracle_set,
                    f"seed={seed}: online emitted a fire absent from the "
                    f"validated oracle — rule fidelity broken: {f}",
                )
            if 0 < len(online) < len(oracle):
                any_strict_subset = True

        # The first Step-1 test proved a naive driver fired MORE than the
        # oracle; the corrected engine must, on at least one seed, fire
        # strictly FEWER (it drops the batch-only phantom tail) — proof
        # the online correction is actually engaged, not a no-op.
        self.assertTrue(
            any_strict_subset,
            "online == oracle on every seed — the online correction is "
            "inert; expected a strict subset on at least one seed",
        )

    def test_empty_and_short_inputs_safe(self):
        self.assertEqual(scan_fires_online([], []), [])
        short = _gen_daily(1, n=40)
        self.assertEqual(
            scan_fires_online(short, daily_candles_to_weekly(short)), [],
            "below min_history must yield no fires",
        )


if __name__ == "__main__":
    unittest.main()
