#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SessionEnd hook — a staleness nudge, nothing more.

This hook deliberately does NOT write, summarize, or update project state. Hooks
run shell commands; they cannot invoke the model, so anything they "summarized"
would be a mechanical guess. Refreshing docs/PROJECT_STATE.md is the job of the
/checkpoint skill, which has the session's actual context.

All this does is count commits since the last checkpoint and say so. Two local
git calls, no network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REL = "docs/PROJECT_STATE.md"
THRESHOLD = 3  # commits before nagging


def git(*a):
    try:
        p = subprocess.run(
            ["git"] + list(a),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return p.stdout.decode("utf-8", "replace").strip()


def main():
    if not git("rev-parse", "--git-dir"):
        return  # not a repo: stay silent

    if not os.path.exists(os.path.join(ROOT, "docs", "PROJECT_STATE.md")):
        say("docs/PROJECT_STATE.md is missing — project continuity is broken. Run /checkpoint.")
        return

    last = git("log", "--format=%H", "-1", "--", REL)
    if not last:
        return

    n = git("rev-list", "--count", "%s..HEAD" % last)
    dirty = git("status", "--porcelain")
    dirty_tracked = 0
    if dirty:
        dirty_tracked = len([x for x in dirty.splitlines() if not x.startswith("??")])

    try:
        n_int = int(n) if n else 0
    except ValueError:
        n_int = 0

    bits = []
    if n_int >= THRESHOLD:
        bits.append("%d commits since the last checkpoint" % n_int)
    if dirty_tracked:
        bits.append("%d uncommitted file(s)" % dirty_tracked)

    if bits:
        say("PROJECT_STATE is drifting: %s. Run /checkpoint to refresh it." % " and ".join(bits))


def say(msg):
    sys.stdout.write(json.dumps({"systemMessage": "[continuity] %s" % msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a nudge is never worth failing a session teardown over
    sys.exit(0)
