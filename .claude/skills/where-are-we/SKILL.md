---
name: where-are-we
description: Orient in this project fast — read docs/PROJECT_STATE.md and reconcile it against actual git/GitHub reality, reporting current workstreams, stopping points, blockers, next actions, and any state that has gone stale or self-contradictory. Use this whenever the user asks "where are we", "what's the status", "what were we working on", "catch me up", "what's next", or opens a new session needing orientation. Read-only: it reports drift, it does not fix it.
---

# /where-are-we

Answer *"where are we with the project?"* accurately, in about a screen of text, and flag
anything in the recorded state that reality no longer supports.

**This command does not modify anything.** Its value comes from being trustworthy at a glance —
if it silently repaired drift, the user would never learn that the state file had rotted, and
the rot would keep happening. Report the drift and let `/checkpoint` fix it.

## Why reconciliation is the point

`docs/PROJECT_STATE.md` is hand-maintained, so it decays between checkpoints. Reading it aloud
would just launder stale claims into confident answers. So read it *and* check it, and be
explicit about which parts you verified.

## Step 1 — Read the recorded state

Read `docs/PROJECT_STATE.md` in full. Note its checkpoint date. The six workstreams are
`seo`, `selection`, `engine`, `portfolio`, `ui-ux`, `platform`.

## Step 2 — Check it against reality

```bash
git log --oneline -15
git status --porcelain
git log --format='%H %ad' --date=short -1 -- docs/PROJECT_STATE.md
git rev-list --count $(git log --format=%H -1 -- docs/PROJECT_STATE.md)..HEAD
gh pr list --state open --limit 10          # optional
gh pr list --state merged --limit 10        # optional
```

If `gh` is missing, unauthenticated, or offline, carry on with git alone and say the PR view was
unavailable. That's a normal condition, not an error.

Look specifically for these drift signatures — each has bitten this project before:

| Signature | What it means |
|---|---|
| Commits since the last checkpoint | State is behind by that many commits — say how many |
| A PR called "in flight" is now merged | `NOW` is stale |
| Work described as shipped, flag still `0` | Shipped but **inert** — not active |
| `BLOCKED` item whose blocker has since been resolved | Phantom blocker |
| Uncommitted tracked changes | Something was left mid-edit |
| Local commits not pushed | Work exists nowhere but this machine |
| Checkpoint date more than ~2 weeks old | Treat the whole file as suspect; trust git |

When state and reality disagree, **git and GitHub win** and you say so plainly. Don't quietly
prefer one — the disagreement is itself the useful finding.

If you need to know *why* something is the way it is, that's project memory's job, not this
file's. Pull the relevant memory rather than speculating.

## Step 3 — Report

Keep it to roughly a screen. Structure:

```
**Where we are** — one or two sentences.

| Workstream | State | Stopped at | Next |
|---|---|---|---|
| seo | ACTIVE | ... | ... |
| ... (all six) |

**Blocked** — only genuine blockers, each with what unblocks it. Say "nothing blocked" if so.

**State check** — checkpoint date, commits since, and any drift found.
Say "PROJECT_STATE.md matches reality" when it does — that's a real result.
```

Then stop. Offer to go deeper on one workstream, or to run `/checkpoint` if you found drift, but
don't do either unprompted.

## What not to do

- **Don't dump commit history.** The user can read git. Cite a PR number when it explains a
  state, not as a log.
- **Don't re-explain the architecture.** `CLAUDE.md` covers it and is already loaded. Describing
  the engine, the three books, or the deploy topology here is wasted screen space.
- **Don't fix anything**, including obvious typos or stale lines in the state file. Report and
  offer.
- **Don't pad.** If four of the six workstreams are idle, the table row says "idle" and that is
  the whole answer. A short accurate report beats a long one.
- **Don't assert a deploy is live** because a PR merged. If it matters to the answer, verify it
  or label it unverified.

## Worked example

Weak — laundered, unverified, and mostly architecture:

> We're working on the SMC trading system, a Python engine using Smart Money Concepts with a
> FastAPI backend on Railway and Next.js on Vercel. Recently we shipped SEO improvements
> including server-rendered stock pages...

Strong — states position, separates verified from recorded, flags drift:

> **Where we are** — SEO Phase 1 is done and verified live; the commercial launch is the big
> unstarted block.
>
> | Workstream | State | Stopped at | Next |
> |---|---|---|---|
> | seo | ACTIVE | PR #182 merged, ISR verified live | internal linking → CWV |
> | selection | idle | Phase 2 merged, `PHASE2_SMC_AS_SCORE=0` (OFF) | accrue shadow data |
> | ... |
>
> **Blocked** — payments/legal need Gaurav's business decisions. Nothing else.
>
> **State check** — checkpointed 2026-08-27, 3 commits since, all docs. One drift: `platform`
> lists `api.stockswithgaurav.com` as a next item, but it still returns NXDOMAIN — unchanged,
> so the entry is accurate.
