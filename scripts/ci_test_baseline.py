"""
scripts/ci_test_baseline.py — compare a pytest run with the explicit known-failure baseline.

    python scripts/ci_test_baseline.py pytest-output.txt tests/known_failures.txt

The backend suite has failures that pre-date backend CI. Rather than skipping or
deselecting them (which would hide them), they are listed one per line in
tests/known_failures.txt with a reason. This check is strict in BOTH directions:

  * a failure/error that is NOT in the baseline  -> CI fails (a regression)
  * a baseline entry that now PASSES              -> CI fails (remove it, so the
                                                     baseline can never silently rot)

Input is pytest's `-rfE` short summary ("FAILED <nodeid> - ..." / "ERROR <nodeid> ...").
A run without pytest's final summary line (a crash) also fails.
"""
from __future__ import annotations

import os
import re
import sys

# pytest's final line: "==== 2 failed, 90 passed in 12.3s ====" normally, or undecorated
# "2 failed, 90 passed in 12.3s (0:00:12)" under -q.
SUMMARY = re.compile(r"^(=+ )?\d+ (passed|failed|error|errors|deselected|skipped)\b.* in [\d.]+s", re.M)


def parse_outcomes(text: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^(FAILED|ERROR) (\S+)", line)
        if m:
            out.add(m.group(2))
    return out


def load_baseline(path: str) -> set[str]:
    items: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if line:
                items.add(line)
    return items


def compare(text: str, baseline: set[str]) -> tuple[int, list[str]]:
    lines: list[str] = []
    summary = SUMMARY.findall(text)
    if not summary:
        return 1, ["pytest produced no final summary line — the run crashed or was cut short."]
    failing = parse_outcomes(text)
    new = sorted(failing - baseline)
    fixed = sorted(baseline - failing)
    final = [ln for ln in text.splitlines() if SUMMARY.match(ln)][-1]
    lines.append(f"pytest: {final.strip('= ')}")
    lines.append(f"failing now: {len(failing)} | baseline: {len(baseline)} | known still failing: "
                 f"{len(failing & baseline)}")
    if new:
        lines.append(f"\nNEW failures/errors not in the baseline ({len(new)}) — regressions:")
        lines += [f"  - {n}" for n in new]
    if fixed:
        lines.append(f"\nBaseline entries that now PASS ({len(fixed)}) — remove them from tests/known_failures.txt:")
        lines += [f"  - {n}" for n in fixed]
    if not new and not fixed:
        lines.append("OK — failures match the documented baseline exactly.")
    return (1 if (new or fixed) else 0), lines


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        text = f.read()
    code, lines = compare(text, load_baseline(argv[2]))
    report = "\n".join(lines)
    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Backend tests vs known-failure baseline\n\n```\n" + report + "\n```\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
