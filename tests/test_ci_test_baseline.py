"""The CI baseline checker must fail on regressions AND on stale baseline entries."""
from __future__ import annotations

from scripts.ci_test_baseline import compare, parse_outcomes

RUN = """\
tests/test_a.py ..F
FAILED tests/test_a.py::test_known - AssertionError
FAILED tests/test_b.py::test_new - KeyError
ERROR tests/test_c.py - ModuleNotFoundError: No module named 'x'
========= 2 failed, 90 passed, 3 deselected, 1 error in 12.3s =========
"""


def test_parse_outcomes_reads_failed_and_error_node_ids():
    assert parse_outcomes(RUN) == {"tests/test_a.py::test_known", "tests/test_b.py::test_new", "tests/test_c.py"}


def test_new_failure_fails_ci():
    code, lines = compare(RUN, {"tests/test_a.py::test_known", "tests/test_c.py"})
    assert code == 1 and any("tests/test_b.py::test_new" in ln for ln in lines)


def test_fixed_baseline_entry_fails_ci():
    base = {"tests/test_a.py::test_known", "tests/test_b.py::test_new", "tests/test_c.py", "tests/test_d.py::test_gone"}
    code, lines = compare(RUN, base)
    assert code == 1 and any("now PASS" in ln for ln in lines)


def test_exact_match_passes():
    code, _ = compare(RUN, {"tests/test_a.py::test_known", "tests/test_b.py::test_new", "tests/test_c.py"})
    assert code == 0


def test_crash_without_summary_fails():
    code, lines = compare("FAILED tests/test_a.py::test_known\n", {"tests/test_a.py::test_known"})
    assert code == 1 and "crashed" in lines[0]


def test_quiet_mode_summary_is_recognised():
    quiet = "FAILED tests/test_a.py::test_known - X\n1 failed, 10 passed, 1 warning in 3.2s (0:00:03)\n"
    code, _ = compare(quiet, {"tests/test_a.py::test_known"})
    assert code == 0
