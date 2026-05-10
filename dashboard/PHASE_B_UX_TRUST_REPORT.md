# Phase B — UX Trust Report

## Reconnect & visibility

- WebSocket **console noise reduced** in production (dev-only debug logging).
- **Tab visibility**: on `visible`, **`requestResync()`** runs first for REST freshness, then existing reconnect logic if the socket is down.
- **Sequence cursor reset** on `ws.onopen` preserves **prices** (registry/snapshot merge) while accepting a new server epoch.

## Status vocabulary

- Transport row still surfaces **WS LIVE / POLLING / RECONNECTING** via existing **`TopBar`** / **`MarketCommandBar`** patterns.

## OI Intelligence

- **Essentials strip** (`OIInterpretationEssentials`) surfaces bias, strength/momentum, and key levels (support/resistance, breakout, trap/reversal zones) from interpretation guidance.
- **Full narrative** moved under **collapsed `<details>`** to cut on-screen complexity ~30% without removing data.

## Analytics

- When intraday + swing + long-term samples are all **thin**, a **single trust banner** explains that metrics are **building** — avoids implying precision without sample depth.

## Watchlist

- Remove action: **optimistic** update with **rollback** on failure.
