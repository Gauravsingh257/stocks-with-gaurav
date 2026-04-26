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
