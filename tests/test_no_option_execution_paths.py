"""Structural guard: no production path may generate or execute options trades.

SRB and every options-trading signal path were retired on 2026-09-23 because the
recorded track record was negative (SRB 19% win rate / PF 0.57 over 58 trades;
option-contract signals PF 0.82 over 155) and because nothing ever executed — the
Kite app has no static IP, so every order was rejected. There was never an env
flag guarding any of it, so the only durable protection is structural: the
emitters and the order path are gone, and this test keeps them gone.

What is deliberately still allowed:
  * engine/oi_short_covering.py and engine/options.py exist — they supply OI
    analytics and index directional context. Neither may emit trade signals.
  * agents/oi_intelligence_agent.py reads the NFO option chain for the read-only
    /oi-intelligence dashboard.
  * manual_trade_handler_v2.py places NSE equity orders for manually-taken
    positions; it is not a signal generator.
  * strategies/second_red_break/{strategy,utils,backtest}.py are kept for offline
    research — but never imported by the live engine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".venv", ".git", "__pycache__", "node_modules", "tests", "backtest",
    "_TEMP_CLAUDE_FILES", "Content Creation", "my-video", "docs", "signal_history",
    "smc_trading_engine",  # standalone library, not run by any Railway service
}

RETIRED_MODULES = [
    "trade_executor_bot",
    "option_monitor_module",
    "strategies.second_red_break.live_scanner",
    "strategies.second_red_break.live_executor",
]

DELETED_FILES = [
    "trade_executor_bot.py",
    "option_monitor_module.py",
    "strategies/second_red_break/live_scanner.py",
    "strategies/second_red_break/live_executor.py",
]


def production_files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name.startswith("_"):  # local scratch/debug harnesses
            continue
        out.append(path)
    return out


FILES = production_files()


def offenders(pattern: str, *, allow: set[str] = frozenset()) -> list[str]:
    rx = re.compile(pattern)
    hits = []
    for path in FILES:
        rel = path.relative_to(ROOT).as_posix()
        if rel in allow:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:110]}")
    return hits


def test_retired_modules_are_gone():
    for rel in DELETED_FILES:
        assert not (ROOT / rel).exists(), f"{rel} was reinstated"


@pytest.mark.parametrize("module", RETIRED_MODULES)
def test_nothing_imports_a_retired_module(module):
    # Match the full dotted path only — a bare tail like "live_scanner" also names
    # unrelated modules (ai_learning.live_scanner).
    patterns = [rf"^\s*(from|import)\s+{re.escape(module)}\b"]
    if "." in module:
        pkg, tail = module.rsplit(".", 1)
        patterns.append(rf"^\s*from\s+{re.escape(pkg)}\s+import\s+.*\b{re.escape(tail)}\b")
    hits = []
    for pattern in patterns:
        hits += offenders(pattern)
    assert not hits, "retired module imported again:\n" + "\n".join(hits)


def test_no_options_trade_signal_is_emitted():
    """No production module may label a signal as an options strategy.

    `risk_management.py` is allow-listed: its SETUP_WIN_RATES table still carries a
    "SECOND-RED-BREAK" win-rate entry (and OI-UNWIND-CE/PE). That table is a lookup
    consulted when sizing a signal — it cannot create one. With no emitter left the
    entries are inert, and risk logic is deliberately out of scope for this cleanup.
    """
    hits = offenders(r"""strategy_name["']\s*:\s*["']OPTIONS-""")
    hits += offenders(r"""["']SECOND-RED-BREAK["']""", allow={"risk_management.py"})
    assert not hits, "options/SRB trade signal emitter found:\n" + "\n".join(hits)


def test_no_option_order_construction():
    """The broker path for option contracts must not exist in production code.

    Resolving an option tradingsymbol and placing a GTT are the two steps that turn
    a signal into a live option position; neither may appear anywhere.
    """
    hits = offenders(r"find_option_tradingsymbol|place_gtt\s*\(")
    assert not hits, "option order construction found:\n" + "\n".join(hits)


def test_option_chain_reads_are_confined_to_the_readonly_dashboard():
    """Reading the NFO chain is fine for analytics — but only in the OI agent."""
    hits = offenders(r"NFO-OPT", allow={"agents/oi_intelligence_agent.py"})
    assert not hits, "option-chain lookup outside the read-only agent:\n" + "\n".join(hits)


def test_live_engine_has_no_srb_path():
    engine = (ROOT / "smc_mtf_engine_v4.py").read_text(encoding="utf-8")
    for needle in ("second_red_break", "_SRB_AVAILABLE", "scan_second_red_break",
                   "execute_srb_trade", "modify_srb_gtt"):
        assert needle not in engine, f"live engine references {needle} again"


def test_option_signal_emitters_removed_from_options_engine():
    """engine/options.py keeps index bias + tick store, never signal emission."""
    src = (ROOT / "engine" / "options.py").read_text(encoding="utf-8")
    for gone in ("def _send_trade_signal", "def _check_standalone_oi_signals",
                 "def _register_option_trade", "def _check_option_exits",
                 "def evaluate_signals"):
        assert gone not in src, f"engine/options.py reinstated {gone}"
    for kept in ("def run_bias_scan", "def lock_bias", "def compute_directional_bias",
                 "class LiveTickStore"):
        assert kept in src, f"engine/options.py lost {kept} (index context must stay)"


def test_oi_short_covering_scan_is_not_called_in_production():
    """The module stays for dashboard history; its scanner must have no caller."""
    hits = offenders(r"scan_short_covering\s*\(",
                     allow={"engine/oi_short_covering.py", "system_deploy_check.py"})
    assert not hits, "OI short-covering scanner called again:\n" + "\n".join(hits)
