# Phase B — Chart Trust Report

## Scope

Terminal **`MiniChart`** (lightweight-charts) + Research chart page were reviewed (no strategy changes).

## Current behavior

- **`MiniChart`** loads OHLC via **`useChartData(symbol)`**, applies **`setData`** when `bars` change, and uses loading/error placeholders to avoid layout collapse.
- **Global LTP / index** for UI strips is now aligned via **`useMergedIndexLtp`** (see Realtime Engine report) so **command bar / intel strip** prices track the same registry as the engine snapshot stream.

## Known gap

- **`MiniChart`** effect dependencies intentionally omit **`symbol`** (eslint comment); switching symbols relies on data hook updates rather than full chart teardown. **Recommendation:** follow-up change should add **`symbol`**-safe lifecycle (dispose chart on symbol change) without blocking Phase B delivery.

## Hydration / flicker

- Loading skeleton and muted empty states already prevent blank flash on errors.
- WS reconnect keeps **snapshot + registry** underlays — no intentional clearing of prices on disconnect.
