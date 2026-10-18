# Telegram alerts vs the portfolio

**STATUS: LIVE** · 2026-10-18 · observability change on branch `fix/phantom-active-ledger-rows`

## The report

> "The stock signals we get in Telegram are somehow not matching our portfolio. We corrected this
> long back, but because our Telegram bot got hacked and we re-established it, the problem started
> again."

## What the system actually does

**Eight independent senders share one bot.** Grepped from the `source=` tag each passes to
`services.telegram_health.send_message`:

| `source` | Sender | Should it match a position? |
|---|---|---|
| `web_portfolio_armed` | `portfolio_manager.py:322` | **yes** — "Awaiting Entry" |
| `web_portfolio_triggered` | `portfolio_manager.py:421` | **yes** — entry tap |
| `web_portfolio_entry` | `portfolio_manager.py:271` | **yes** — live sync entry |
| `web_portfolio_exit` | `portfolio_manager.py:449` | **yes** |
| `web_entry_tap` | `entry_trigger_service.py:181` | **yes** |
| `web_research_exit` | `trade_tracker.py:499` | no — research recommendation, not a position |
| `web_fvg_tap` | `fvg_tap_engine.py:254` | **no** — alert mode since 2026-05-26, never auto-traded |
| `web_equity_sm` | `equity_state_machine.py:412` | **no** — tracked book, alert mode |
| `engine` | `smc_mtf_engine_v4.py` | n/a — `INDEX_ONLY=True`, so NIFTY/BANKNIFTY, not stocks |

**So part of the "mismatch" is correct behaviour.** `web_fvg_tap` and `web_equity_sm` are
validation-phase experiments that deliberately alert without taking a position, and
`web_research_exit` fires on research rows. Three of the nine streams are *supposed* not to appear
in the portfolio.

## Token and chat routing

The portfolio streams all resolve the same pair:

```python
bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat_id   = os.getenv("TELEGRAM_CHAT_ID", "") or os.getenv("SMC_PRO_CHAT_ID", "")
```

The two validation streams have **their own** env vars, deliberately separate:

```python
token = os.getenv("FVG_TAP_TG_TOKEN", "");    chat = os.getenv("FVG_TAP_TG_CHAT", "")
token = os.getenv("EQUITY_SM_TG_TOKEN", "");  chat = os.getenv("EQUITY_SM_TG_CHAT", "")
```

Neither has a fallback — unset chat means no send. So a validation stream can only reach the main
channel if its `*_TG_CHAT` was **explicitly set to the main channel id**.

## What production shows

`/api/system/telegram-health`, 2026-10-18:

| Chat (masked) | Status | total_ok | total_fail | last source | last OK | last fail |
|---|---|---:|---:|---|---|---|
| `-100…6791` | OK | 195 | 43 | `engine` | 0.3h | 169.7h |
| `-100…7157` | OK | 3 | 1 | `engine` | 79.0h | 221.6h |
| `9255…0718` | OK | 2 | 0 | `web_fvg_tap` | 97.0h | — |

Two readings matter:

1. **Delivery is healthy now.** The 43 failures on the main channel were `PEER_ID_INVALID` /
   `HTTP 404`, and the most recent is ~169.7h old — about **2026-10-11**, the day the token was
   rotated and `@swg_stocks_alerts_bot` went live. That is the rotation window, and it has closed.
   The mismatch is therefore **not** lost messages.
2. **Three destinations are receiving traffic**, and FVG-Tap last landed in a *private* chat
   (`9255…0718`, no `-100` prefix), not the channel. So at the time of that send the separation was
   intact. Two `-100` channels both showing `engine` as last source is the part that needs an
   explanation.

## The gap that made this unanswerable

`record_send_result` wrote **per-message detail only for failures** (`telegram:health:failures`)
and aggregated successes **per destination chat**. There was no record of which *stream* sent a
successful alert. The healthy case was the blind one, so an alert the owner saw in the chat could
not be attributed to a sender at all — the question could only be answered by reading Railway env
vars by hand.

**Fixed** (commit `f748bda`): per-source counters alongside the destination hashes.
`/api/system/telegram-health` now returns a `sources` array — each stream with its masked last
chat, total ok/fail, and the age of its last success and failure — plus `SOURCE_KINDS`, which
states which streams are *supposed* to correspond to a position so a reader can tell an expected
mismatch from a real one. An unregistered sender reports `unknown` rather than inheriting a
reassuring label. Observability only: no sender, token, chat or trading behaviour changed.

## The one thing code cannot answer — needs Gaurav

Whether the separation is still configured. Three env values on the **`web`** service:

1. `FVG_TAP_TG_CHAT` — if this equals `TELEGRAM_CHAT_ID`, every FVG-Tap alert is landing in the
   main channel and will never match a position. **Most likely cause of the regression**, because
   the rotation set the new bot "live everywhere".
2. `EQUITY_SM_TG_CHAT` — same test.
3. `TELEGRAM_CHAT_ID` — which of `-100…6791` / `-100…7157` it is, and whether the other channel
   belongs to a service or project that should no longer be sending. The 2026-09-02 incident was a
   second Railway project (`affectionate-luck`) running the whole web app on its own ephemeral
   database with the same bot token; it was silenced by deleting its `TELEGRAM_BOT_TOKEN`. If the
   rotation re-populated that project, the phantom alerts return — and the symptom ("started again
   after we re-established the bot") fits exactly.

Also confirm `@swg_stocks_alerts_bot` is an admin of **only** the channel you intend.

## Note

The *content* of portfolio alerts is a separate question from their routing. Until PR #237 lands,
`/api/lifecycle` reported 201 open positions against 32 real ones, so any comparison of "what
Telegram said" with "what the track record shows" was being made against a corrupt ledger. Settle
the routing questions above, then re-compare on clean data.
