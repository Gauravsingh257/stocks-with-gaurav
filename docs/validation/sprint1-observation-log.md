# Sprint 1 — Observation Log

> Living document for the 5-trading-day validation window. Log **every** observation
> (yours, beta users', analytics anomalies) as a row. Triage into the four buckets.
> At the end, the highest-severity + highest-frequency rows drive the Sprint 2 backlog.
>
> **Window:** Day 1 → Day 5 of trading after Sprint 1 deploy.
> **Rule (Task 4):** during this window we only fix bugs / broken UX / performance /
> mobile / copy. Everything else becomes a row here tagged `→ Sprint 2 backlog`.

## How to log
Add a row to the matching bucket. Keep it one line where possible.

- **Severity:** Critical (blocks use / data wrong / crash) · Major (frustrating, common) ·
  Minor (cosmetic, rare) · Idea (enhancement, not a defect).
- **Frequency:** how often seen — `1x`, `several`, `most sessions`, `every time`.

| Field | Notes |
|-------|-------|
| Date | Day of observation |
| Feature | Command Center / NBA / Watchlist / Brief / Nav / Auth / Mobile / Other |
| Problem | What actually happened |
| Frequency | 1x · several · most · every |
| Severity | Critical · Major · Minor · Idea |
| Suggested improvement | The fix or the Sprint 2 idea |

---

## 🔴 Critical
| Date | Feature | Problem | Freq | Suggested improvement |
|------|---------|---------|------|-----------------------|
| | | | | |

## 🟠 Major
| Date | Feature | Problem | Freq | Suggested improvement |
|------|---------|---------|------|-----------------------|
| | | | | |

## 🟡 Minor
| Date | Feature | Problem | Freq | Suggested improvement |
|------|---------|---------|------|-----------------------|
| | | | | |

## 💡 Ideas (→ Sprint 2 backlog)
| Date | Feature | Idea | Why it matters |
|------|---------|------|----------------|
| | | | |

---

## Seed observations (from launch validation, 2026-07-11)
Pre-filled from the deploy verification so we start with a real baseline:

| Date | Feature | Problem | Freq | Severity | Suggested improvement |
|------|---------|---------|------|----------|-----------------------|
| 07-11 | Command Center · Brief | "Reads aggregate regime as NEUTRAL…" reads as a fragment | every | Minor | Rewrite the backend brief sentence in `command_center_service.py` (Sprint 2 copy) |
| 07-11 | Command Center · Mood | Market Mood shows regime only — no live global cues (Gift Nifty/Dow/VIX) though they're in the top ticker | every | Idea | Wire ticker cues into the mood read → Sprint 2 backlog |
| 07-11 | NBA | Each page's NBA card fetches `/api/command-center` independently (up to 4 calls) | every | Minor | Share one fetch via context/provider |
| 07-11 | Watchlist feed | Depth depends on backend `.feed` population — may be thin | TBD | Minor | Observe with a real logged-in watchlist during market hours |
