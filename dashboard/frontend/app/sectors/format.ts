import type { SectorQuadrant } from "@/lib/api";

export const QUADRANT_ORDER: SectorQuadrant[] = ["leading", "improving", "weakening", "lagging"];

export const QUADRANT_LABEL: Record<SectorQuadrant, string> = {
  leading: "Leading",
  improving: "Improving",
  weakening: "Weakening",
  lagging: "Lagging",
};

/** What each quadrant measures — descriptive, never an instruction. */
export const QUADRANT_HINT: Record<SectorQuadrant, string> = {
  leading: "Stronger than the benchmark, and relative strength still rising.",
  improving: "Weaker than the benchmark, but relative strength rising.",
  weakening: "Stronger than the benchmark, but relative strength falling.",
  lagging: "Weaker than the benchmark, and relative strength falling.",
};

export const QUADRANT_VAR: Record<SectorQuadrant, string> = {
  leading: "var(--q-leading)",
  improving: "var(--q-improving)",
  weakening: "var(--q-weakening)",
  lagging: "var(--q-lagging)",
};

export function num(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
}

export function signed(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : `${v.toFixed(digits)}%`;
}

export function crore(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1e5) return `${(v / 1e5).toFixed(2)}L Cr`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K Cr`;
  if (Math.abs(v) >= 10) return `${v.toFixed(0)} Cr`;
  return `${v.toFixed(1)} Cr`;
}

export function longDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

/** `written_at` is stored as UTC "YYYY-MM-DD HH:MM:SS" by SQLite; show it in IST. */
export function writtenIst(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(`${v.replace(" ", "T")}Z`);
  if (Number.isNaN(d.getTime())) return v;
  return `${d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  })} IST`;
}

export const SOURCE_LABEL: Record<string, string> = {
  nse_official: "NSE official",
  manual: "Hand-assigned",
  provider: "Provider industry",
  provider_cache: "Provider industry",
  classifier: "Classifier",
};
