---
name: todo
description: Answer "where am I and what do I do next?" across the whole operation — the trading system, stockswithgaurav.com, and the dated commitments on the calendar. Use whenever the user asks what to work on, what's pending, what's next, what they're forgetting, or opens a session wanting a prioritised action list rather than a status report.
---

# /todo

Produce a **short, ordered list of what to do next** — across the trading system, the website,
and the calendar. Not a status report: `/where-are-we` already does that. This answers *"what
should I actually do now?"*

The value is **ordering**, not completeness. A list of thirty things is the same as no list.

## Gather (cheap, parallelisable)

```bash
git log --oneline -8 && git status --porcelain | grep -v '^??' | head
python -m scripts.exit_rule_health            # non-zero = a rule is silently dead
curl -s "$API/api/portfolio/counts"           # slot room per book
curl -s "$API/api/system/health"              # engine live? token fresh?
grep -cE '^\- \[ \].*🔒' LAUNCH_CHECKLIST.md   # open hard launch gates
```
`$API` = `https://web-production-2781a.up.railway.app`.

Then read `docs/PROJECT_STATE.md` — every workstream's `NEXT` and `BLOCKED`.

Then the calendar (`collab.shreesingh@gmail.com`, `list_events`, next ~45 days). It carries
**two unrelated streams** and both matter:
- **Trading-system reviews** — shadow/alert experiments with forced promote/retire/extend decisions.
- **Brand/influencer business** — payment follow-ups and deliverable deadlines, each with the
  contact, amount and terms in the description. These are money and reputation with hard dates;
  they usually outrank engineering work even though this is a code repo.

If a source fails (gh offline, MCP down, API 502), say so in one line and carry on. A partial
list delivered now beats a complete one after a retry loop.

## Order by what it costs to be late

1. **Overdue or due today** — a missed brand deliverable or an unpaid invoice costs real money.
2. **Dated this week** — calendar reviews, expiring data.
3. **Decisions only Gaurav can make** — these block everything downstream and are usually the
   real bottleneck, not the engineering.
4. **Highest-leverage engineering** — prefer the upstream fix. A selection bug outranks an exit
   rule, because everything downstream inherits it.
5. **Everything else** — name it, don't detail it.

Two ordering rules worth stating because they are easy to get wrong:

- **Time-critical beats important.** Redis TTLs, 30/60-day payment terms and content deadlines do
  not wait for a better moment.
- **Unverified findings are not tasks.** "Confidence score is inversely correlated with outcome"
  is a *hypothesis to test*, not a fix to ship. List it as "verify X", never as "fix X".

## Output

Keep it to roughly a screen:

```
**Right now** — one sentence on where the project stands.

## 🔴 Today / overdue
- [thing] — why it matters, what "done" looks like

## 📅 This week
- [date] [thing]

## 🤔 Waiting on you (decisions)
- [thing] — the options, and what happens if it keeps waiting

## 🔧 Next engineering work (ordered)
1. [thing] — one line on why it's first

## 🟢 Running by itself — no action
- [thing]
```

End with **one** recommended next action and why. One, not three.

## What not to do

- **Don't restate `/where-are-we`.** Status is context here, not the deliverable.
- **Don't list completed work.** git has it. The only reason to mention something shipped is if
  it is shipped-but-unproven and still needs watching.
- **Don't invent deadlines.** If something has no date, it goes under engineering, not "this week".
- **Don't pad.** If a workstream has nothing to do, leave it out entirely — six headers with
  "nothing pending" is noise.
- **Don't fix anything.** This command reports and orders. Offer, then stop.
