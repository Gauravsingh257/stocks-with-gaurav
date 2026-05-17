/** NSE cash session in IST (09:15–15:30; preopen 09:00–09:15). */
export function getMarketSession(): "OPEN" | "PREOPEN" | "CLOSED" {
  const now = new Date();
  const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const hour = ist.getHours();
  const minute = ist.getMinutes();
  const minutes = hour * 60 + minute;
  const open = 9 * 60 + 15;
  const preopen = 9 * 60;
  const close = 15 * 60 + 30;
  if (minutes >= open && minutes <= close) return "OPEN";
  if (minutes >= preopen && minutes < open) return "PREOPEN";
  return "CLOSED";
}

/** True on Sat/Sun in IST — NSE cash is shut all weekend. */
export function isWeekendIST(): boolean {
  const ist = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const day = ist.getDay(); // 0 Sun … 6 Sat
  return day === 0 || day === 6;
}

/** True only when the NSE cash market is genuinely live right now. */
export function isMarketLive(): boolean {
  return !isWeekendIST() && getMarketSession() === "OPEN";
}

/**
 * Calm, trust-preserving phrase for off-hours data states. Returns null
 * when the market is live (callers should then surface real errors
 * normally). Off-hours, a stale/empty feed is EXPECTED — not an error —
 * so the UI must read calm, never alarming.
 */
export function offHoursNotice(): string | null {
  if (isWeekendIST())
    return "Market closed (weekend) — showing last verified snapshot. Next scan Monday 08:30 IST.";
  const s = getMarketSession();
  if (s === "OPEN") return null;
  if (s === "PREOPEN")
    return "Pre-open — showing last verified snapshot. Engine scan runs 08:30 IST.";
  // CLOSED on a weekday: before 09:00 or after 15:30
  const ist = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const beforeOpen = ist.getHours() * 60 + ist.getMinutes() < 9 * 60;
  return beforeOpen
    ? "Market closed — showing last verified snapshot. Next scan 08:30 IST."
    : "Market closed for the day — showing last verified snapshot. Next scan 08:30 IST (Mon–Fri).";
}
