/**
 * Canonical symbol + entity keys for cross-surface consistency (watchlist, research, WS).
 */

export function normalizeSymbol(raw: string): string {
  return String(raw).replace(/^NSE:/i, "").trim().toUpperCase();
}
