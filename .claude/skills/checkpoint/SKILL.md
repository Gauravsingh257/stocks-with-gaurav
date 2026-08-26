---
name: checkpoint
description: Refresh docs/PROJECT_STATE.md at the end of a work session so the next session knows exactly where things stopped. Use this whenever the user says "checkpoint", "save state", "update project state", "wrap up", "log where we got to", or is finishing a work session on this project. Also use it proactively when a work arc completes (a PR merges, a branch lands, an investigation concludes) and PROJECT_STATE.md would otherwise go stale.
---

# /checkpoint

Update `docs/PROJECT_STATE.md` so a brand-new session can answer *"where are we?"* without
the user re-explaining anything.

This is the one deliberate manual step in the continuity system. Hooks run shell commands and
cannot call a model, so nothing else in the system can do this job — only you, right now, know
what this session actually meant. Treat that as the point of the exercise: you are converting
*session context* into *durable state* before the context evaporates.

## What you are and aren't updating

| File | Owns | Touch it here? |
|---|---|---|
| `docs/PROJECT_STATE.md` | Current state: NOW, STOPPED AT, NEXT, BLOCKED | **Yes — this is the job** |
| `CLAUDE.md` | Stable architecture, commands, conventions | Only if architecture genuinely changed |
| Project memory | Durable *why* — decisions, traps, rejected approaches | Only when the necessity test below passes |
| git / `gh` | What changed, exactly | **Never.** Do not restate commit history |

The six workstreams are fixed: `seo`, `selection`, `engine`, `portfolio`, `ui-ux`, `platform`.
Don't invent new ones. If work doesn't fit, it almost certainly belongs to `platform`.

## Step 1 — Gather evidence

Run these together; they're cheap:

```bash
git log --oneline -15
git status --porcelain
git rev-list --count @{upstream}..HEAD 2>/dev/null   # unpushed commits
git log --format=%H -1 -- docs/PROJECT_STATE.md      # last checkpoint
gh pr list --state open --limit 10                   # optional; skip if it fails
```

Then read the current `docs/PROJECT_STATE.md` in full. You are editing it, not rewriting it —
most of it is probably still true.

## Step 2 — Place each piece of work on the evidence ladder

This is the part that matters, and the part that's easy to get wrong. **A commit proves work
happened. It does not prove the work is done.** Marking something complete because a commit
exists is the single most damaging error you can make here, because the next session will build
on a false premise and won't discover the truth until something breaks.

Place every piece of work honestly:

| Evidence | Status | Where it goes |
|---|---|---|
| Discussed, nothing written | not started | `NEXT` |
| Uncommitted edits in the tree | in flight | `NOW` + `STOPPED AT` |
| Committed locally, not pushed | in flight | `NOW` + `STOPPED AT` (say "not pushed") |
| Pushed, PR open | in flight | `NOW` |
| PR merged to `main` | shipped — **not necessarily live** | `STOPPED AT` |
| Merged **and** verified running in prod | done | `Recently shipped` |
| Merged but behind an OFF flag | shipped and **inert** | `STOPPED AT`, say the flag is OFF |
| Backtested and rejected | **done, negatively** | memory; note in `NEXT` so it isn't retried |

Two project-specific traps worth checking every time:

- **Production deploys from `main`.** A merged PR is not a live change until Railway and Vercel
  have actually deployed it. If it matters, verify rather than assume.
- **Flags default to the old behaviour.** Merged code behind `FLAG=0` changes nothing in
  production. "Shipped" and "active" are different words here; use them precisely.

When you can't tell whether something is done, say so in the file — "merged, deploy unverified"
is far more useful to the next session than a confident wrong answer.

## Step 3 — Edit PROJECT_STATE.md

Update, in place:

- The **checkpoint date** in the header.
- The **one-paragraph summary**, if the shape of the project changed. Usually it didn't.
- The **workstream table** (state + one line each).
- For each workstream that moved: `NOW`, `STOPPED AT`, `NEXT` (ordered), `BLOCKED`.
- `Recently shipped` — only PR *ranges* mapped to workstreams, never a changelog.

Leave untouched anything that didn't change. A checkpoint that rewrites the whole file every
time produces noisy diffs and hides what actually moved.

**Keep it short.** The file's value is that it is quickly readable — it should stay roughly its
current length. If a workstream's section is growing past ~12 lines, you're putting detail in
the wrong place: the *why* belongs in memory, the *what changed* belongs in git, and the code
belongs in the code.

### What counts as BLOCKED

Only list something as blocked when a *specific* thing must happen before work can continue, and
name that thing. "Waiting for more shadow data" is not blocked — that's gated on time, and
calling it blocked makes the blocker list useless. "Needs `vercel login`, which requires browser
OAuth from Gaurav" is blocked, and it says who unblocks it.

## Step 4 — Consider memory, but be reluctant

Write a memory file only when a fact passes all three:

1. **Durable** — still true in three months, after this arc closes.
2. **Non-derivable** — not recoverable by reading the code, the commits, or PROJECT_STATE.
3. **Decision-changing** — a future session would do something *worse* without it.

Things that pass: a rejected approach and why it was rejected; a trap that cost real debugging
time; a constraint the code doesn't reveal; a deliberate decision that looks like a bug.

Things that fail: what a PR did (git has it); current status (PROJECT_STATE has it); how a module
works (the code has it, CLAUDE.md summarizes it).

If it passes, write one file to the auto-memory directory with `name`, `description`,
`metadata.type` (`project` for arcs, `feedback` for how-to-work guidance), plus **Why:** and
**How to apply:** lines. Add one line to `MEMORY.md`. Link related memories with `[[name]]`.
Prefer updating an existing memory over creating a near-duplicate — check first.

If nothing passes, write nothing. Most checkpoints should not produce a memory. An over-full
memory directory is worse than a sparse one, because it dilutes what gets recalled.

## Step 5 — Report back, briefly

Tell the user, in a few lines:
- what moved, per workstream
- what you deliberately did **not** mark complete, and why
- whether you wrote a memory (and if not, that you judged it unnecessary)

Then offer to commit. Don't commit unprompted unless the user has already said to.

## Worked example

Session ended with the ISR fix merged, plus a local docs commit that isn't pushed.

Wrong — treats a merge as done, and restates git:

```
## seo — COMPLETE
Shipped PR #182 (restore ISR), PR #181 (SSR stock pages), PR #180...
```

Right — separates shipped from verified, keeps the stopping point precise:

```
## `seo` — ACTIVE
**NOW** — nothing in flight.
**STOPPED AT** — PR #182 merged 2026-08-26; ISR fix verified live
(`X-Nextjs-Prerender: 1`, sitemap 2,117 URLs). Items 1–5 of the SEO plan done.
**NEXT** — 1. internal linking  2. Core Web Vitals  3. reinstall Speed Insights
**BLOCKED** — nothing. (`vercel` CLI is logged out if CLI work comes up.)
```
