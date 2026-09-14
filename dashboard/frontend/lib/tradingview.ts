/**
 * NSE ticker → TradingView symbol, for every "Open in TradingView" link.
 *
 * TradingView resolves a bare ticker to whichever exchange it ranks first, so
 * "FCL" opens ASX:FCL (FINEOS) rather than Fineotex Chemical on the NSE. Every
 * link therefore carries the exchange explicitly. TradingView also spells NSE
 * tickers differently from the exchange: a hyphen becomes an underscore
 * (BAJAJ-AUTO → NSE:BAJAJ_AUTO, NAM-INDIA → NSE:NAM_INDIA), while "&" is kept
 * (M&M → NSE:M&M), both checked against TradingView's symbol search.
 *
 * This is for links to tradingview.com only. Embedded TradingView widgets are
 * not licensed to show NSE prices at all, which is why /stock/<symbol> draws its
 * own chart (components/NseStockChart).
 */

/** The TradingView ticker for an NSE symbol, without the exchange prefix. */
export function tradingViewTicker(symbol: string): string {
  return symbol
    .trim()
    .toUpperCase()
    .replace(/^NSE:/, "")
    .replace(/\.NS$/, "")
    .replace(/-/g, "_");
}

/** "NSE:<ticker>" — the exact instrument, never a bare ticker. */
export function tradingViewSymbol(symbol: string): string {
  return `NSE:${tradingViewTicker(symbol)}`;
}

/** A tradingview.com chart link for an NSE symbol. */
export function tradingViewChartUrl(symbol: string, interval?: string): string {
  const query = `symbol=${encodeURIComponent(tradingViewSymbol(symbol))}`;
  return `https://www.tradingview.com/chart/?${query}${interval ? `&interval=${encodeURIComponent(interval)}` : ""}`;
}
