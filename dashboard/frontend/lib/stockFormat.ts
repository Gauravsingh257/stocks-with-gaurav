/**
 * Formatting for facts shown on /stock/<symbol>.
 *
 * The key metrics (server) and the analysis card (client) show some of the same
 * facts — P/E, market cap, price. Both format through here so one value is never
 * written two ways on one page ("222x" vs "PE 222.0").
 */

const DASH = "—";

function finite(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function inr(value: number | null | undefined): string {
  return finite(value) ? `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}` : DASH;
}

export function num(value: number | null | undefined, suffix = ""): string {
  return finite(value) ? `${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}${suffix}` : DASH;
}

export function signedPct(value: number | null | undefined): string {
  if (!finite(value)) return DASH;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}%`;
}

export function crore(value: number | null | undefined): string {
  if (!finite(value)) return DASH;
  if (value >= 100000) return `₹${(value / 100000).toFixed(2)} lakh Cr`;
  return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr`;
}

/** Parse a timestamp; naive "YYYY-MM-DD HH:MM:SS" values (SQLite) are UTC. */
function parseTimestamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = value.trim().replace(" ", "T");
  const withZone = /([zZ]|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`;
  const date = new Date(withZone);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "12 Sept 2026" (IST) for a snapshot timestamp. */
export function snapshotDate(value: string | null | undefined): string | null {
  const date = parseTimestamp(value);
  return date
    ? date.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" })
    : null;
}

/** The IST trading day ("2026-09-15") a timestamp falls on — comparable with candle dates. */
export function istDay(value: string | null | undefined): string | null {
  const date = parseTimestamp(value);
  return date ? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(date) : null;
}

/** "15 Sept, 12:35 IST". */
export function istDateTime(value: string | null | undefined): string | null {
  const date = parseTimestamp(value);
  if (!date) return null;
  const day = date.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
  const time = date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  return `${day}, ${time} IST`;
}

/** Plain label for a services/price_resolver source. */
export function quoteSourceLabel(source: string | null | undefined): string {
  const s = (source ?? "").toLowerCase();
  if (s.includes("ws_cache") || s.includes("kite")) return "Live";
  if (s.includes("yf") || s.includes("delay")) return "Delayed";
  if (s && s !== "unknown") return "Last close";
  return "Latest";
}
