#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SessionStart hook — inject "where are we?" context into every session.

Design constraints:
  * Cheap. Local git only, plus one optional `gh` call with a hard timeout.
  * Never fails. Any error degrades to a partial report; the hook always exits 0
    with valid JSON, because a crashing hook is worse than a missing one.
  * Never duplicates CLAUDE.md (architecture) or project memory (why). It reports
    only volatile state: branch, commits, working tree, PRs, and the NOW/BLOCKED
    sections of docs/PROJECT_STATE.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = os.path.join(ROOT, "docs", "PROJECT_STATE.md")


def run(args, timeout=5):
    """Run a command, returning stripped stdout or None. Never raises."""
    try:
        p = subprocess.run(
            args,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    try:
        return p.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return None


def git(*a, **kw):
    return run(["git"] + list(a), **kw)


def section(md, heading):
    """Extract each **HEADING** block, labelled with the `## workstream` it sits under.

    Returns [(workstream, [lines...]), ...]. Without the workstream label the six
    identical "**NOW**" lines are indistinguishable and therefore useless.
    """
    blocks, cur_ws, grabbing, buf = [], "?", False, []

    def flush():
        if buf:
            blocks.append((cur_ws, list(buf)))
            del buf[:]

    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## "):
            flush()
            grabbing = False
            # "## `seo` — ACTIVE" -> "seo — ACTIVE"
            cur_ws = s[3:].replace("`", "").strip()
            continue
        if s.startswith("**" + heading):
            flush()
            grabbing = True
            rest = s[len("**" + heading):].lstrip("*").strip()
            rest = rest.lstrip("—-").strip()
            if rest:
                buf.append(rest)
            continue
        if grabbing:
            if s.startswith("**"):
                flush()
                grabbing = False
                continue
            if s:
                buf.append(s)
    flush()
    return blocks


def render(blocks, limit):
    out = []
    for ws, lines in blocks:
        body = " ".join(lines).strip()
        if not body:
            continue
        if len(body) > limit:
            body = body[:limit].rsplit(" ", 1)[0] + " ..."
        out.append("  [%s] %s" % (ws, body))
    return out


def main():
    L = []

    # ---- git: branch, head, upstream -------------------------------------
    if not git("rev-parse", "--git-dir"):
        emit("Not a git repository — no project state available.")
        return

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":  # detached
        desc = git("describe", "--all", "--contains", "HEAD") or git("rev-parse", "--short", "HEAD")
        branch = "DETACHED HEAD at %s" % (desc or "unknown")
    L.append("Branch: %s" % (branch or "unknown"))

    ahead = git("rev-list", "--count", "@{upstream}..HEAD")
    behind = git("rev-list", "--count", "HEAD..@{upstream}")
    if ahead is not None and behind is not None:
        if ahead != "0" or behind != "0":
            L.append("Upstream: %s ahead, %s behind" % (ahead, behind))
    else:
        L.append("Upstream: none (branch not pushed)")

    # ---- working tree ----------------------------------------------------
    porcelain = git("status", "--porcelain") or ""
    tracked = [x for x in porcelain.splitlines() if not x.startswith("??")]
    untracked = [x for x in porcelain.splitlines() if x.startswith("??")]
    if tracked:
        L.append("Uncommitted changes (%d tracked):" % len(tracked))
        for line in tracked[:10]:
            L.append("  %s" % line)
        if len(tracked) > 10:
            L.append("  ... and %d more" % (len(tracked) - 10))
    else:
        L.append("Working tree: clean (tracked files)")
    if untracked:
        L.append("Untracked: %d path(s) — long-standing in this repo, usually not actionable" % len(untracked))

    # ---- recent commits --------------------------------------------------
    log = git("log", "-8", "--format=%h %ad %s", "--date=short")
    if log:
        L.append("")
        L.append("Recent commits:")
        for line in log.splitlines():
            L.append("  %s" % line)

    # ---- open PRs (optional; never allowed to hang) ----------------------
    L.append("")
    gh_out = run(
        ["gh", "pr", "list", "--state", "open", "--limit", "10",
         "--json", "number,title,headRefName,isDraft",
         "--template", "{{range .}}#{{.number}} {{.title}} [{{.headRefName}}]{{if .isDraft}} (draft){{end}}\n{{end}}"],
        timeout=8,
    )
    if gh_out is None:
        L.append("Open PRs: unavailable (gh missing, unauthenticated, or offline) — not an error")
    elif not gh_out:
        L.append("Open PRs: none")
    else:
        L.append("Open PRs:")
        for line in gh_out.splitlines():
            if line.strip():
                L.append("  %s" % line.strip())

    # ---- PROJECT_STATE.md: NOW + BLOCKED --------------------------------
    L.append("")
    if not os.path.exists(STATE):
        L.append("docs/PROJECT_STATE.md is MISSING — current state is unknown. Recreate it.")
    else:
        try:
            with open(STATE, "rb") as fh:
                md = fh.read().decode("utf-8", "replace")
        except Exception:
            md = ""
        stamp = ""
        for line in md.splitlines()[:12]:
            if "last checkpoint" in line:
                stamp = line.strip().lstrip("> ")
                break
        L.append("docs/PROJECT_STATE.md — %s" % (stamp or "no checkpoint date found"))

        # how stale is it, in commits?
        since = git("log", "--format=%H", "-1", "--", "docs/PROJECT_STATE.md")
        if since:
            n = git("rev-list", "--count", "%s..HEAD" % since)
            if n and n != "0":
                L.append("  ** %s commits since the last checkpoint — run /checkpoint to refresh **" % n)

        now = render(section(md, "NOW"), 200)
        stopped = render(section(md, "STOPPED AT"), 200)
        blocked = render(section(md, "BLOCKED"), 240)
        if now:
            L.append("")
            L.append("NOW:")
            L.extend(now)
        if stopped:
            L.append("")
            L.append("STOPPED AT:")
            L.extend(stopped)
        if blocked:
            L.append("")
            L.append("BLOCKED:")
            L.extend(blocked)

    L.append("")
    L.append("Sources of truth: CLAUDE.md = architecture · docs/PROJECT_STATE.md = current state "
             "· project memory = why · git/gh = history. Read the full PROJECT_STATE.md before "
             "answering \"where are we?\" in depth.")

    emit("\n".join(L))


def emit(text):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "<project-state-snapshot>\n%s\n</project-state-snapshot>" % text,
        },
        "suppressOutput": True,
    }
    out = json.dumps(payload)
    try:
        sys.stdout.write(out)
    except Exception:
        sys.stdout.write(json.dumps({"suppressOutput": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # absolute last resort — never break session start
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "project-state hook failed: %s" % exc,
            },
            "suppressOutput": True,
        }))
    sys.exit(0)
