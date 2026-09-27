"use client";

import { useMemo, useState } from "react";
import type { SectorRotationRow } from "@/lib/api";
import { QUADRANT_LABEL, QUADRANT_VAR, num, pct, signed } from "./format";
import styles from "./sectors.module.css";

type Col = {
  key: string;
  label: string;
  title: string;
  value: (s: SectorRotationRow) => number | null;
  show: (s: SectorRotationRow) => string;
  /** Neutral point and the distance from it that reads as a full-strength tint. */
  neutral?: number;
  span?: number;
};

const COLS: Col[] = [
  { key: "rs_ratio", label: "Rel. strength", title: "Relative strength vs the selected benchmark (100 = in line)",
    value: (s) => s.rs_ratio, show: (s) => num(s.rs_ratio, 2), neutral: 100, span: 4 },
  { key: "rs_momentum", label: "Momentum", title: "Momentum of relative strength (100 = flat)",
    value: (s) => s.rs_momentum, show: (s) => num(s.rs_momentum, 2), neutral: 100, span: 2 },
  { key: "rs_accel", label: "Accel.", title: "Change in momentum over the last few bars",
    value: (s) => s.rs_accel, show: (s) => signed(s.rs_accel), neutral: 0, span: 2 },
  { key: "pct_above_20", label: ">20D", title: "% of liquid stocks above their 20-day average",
    value: (s) => s.pct_above_20, show: (s) => pct(s.pct_above_20), neutral: 50, span: 40 },
  { key: "pct_above_50", label: ">50D", title: "% of liquid stocks above their 50-day average",
    value: (s) => s.pct_above_50, show: (s) => pct(s.pct_above_50), neutral: 50, span: 40 },
  { key: "pct_above_200", label: ">200D", title: "% of liquid stocks above their 200-day average",
    value: (s) => s.pct_above_200, show: (s) => pct(s.pct_above_200), neutral: 50, span: 40 },
  { key: "hl", label: "52W H / L", title: "Stocks at a 52-week closing high / low",
    value: (s) => (s.new_highs_52w ?? 0) - (s.new_lows_52w ?? 0),
    show: (s) => `${s.new_highs_52w ?? "—"} / ${s.new_lows_52w ?? "—"}`, neutral: 0, span: 8 },
  { key: "vol_ratio", label: "Volume", title: "Typical stock's volume vs its 50-day median (1.0 = normal)",
    value: (s) => s.vol_ratio, show: (s) => num(s.vol_ratio, 2), neutral: 1, span: 0.5 },
  { key: "up_turnover_pct", label: "Up value", title: "Share of traded value in stocks that rose",
    value: (s) => s.up_turnover_pct, show: (s) => pct(s.up_turnover_pct), neutral: 50, span: 40 },
  { key: "dispersion", label: "Dispersion", title: "Spread of stock returns over the lookback (higher = less uniform)",
    value: (s) => s.dispersion, show: (s) => num(s.dispersion, 1) },
  { key: "n_constituents", label: "Stocks", title: "Liquid stocks in the sector reading",
    value: (s) => s.n_constituents, show: (s) => String(s.n_constituents ?? "—") },
];

function tint(col: Col, v: number | null): string | undefined {
  if (v === null || col.neutral === undefined || !col.span) return undefined;
  const d = Math.max(-1, Math.min(1, (v - col.neutral) / col.span));
  if (Math.abs(d) < 0.04) return undefined;
  const tone = d > 0 ? "var(--tone-good-fg)" : "var(--tone-weak-fg)";
  return `color-mix(in srgb, ${tone} ${Math.round(Math.abs(d) * 32)}%, transparent)`;
}

export default function SectorHeatmap({ sectors, selected, onSelect }: {
  sectors: SectorRotationRow[];
  selected: string | null;
  onSelect: (sector: string) => void;
}) {
  const [sortKey, setSortKey] = useState("rs_ratio");
  const [asc, setAsc] = useState(false);

  const rows = useMemo(() => {
    const col = COLS.find((c) => c.key === sortKey);
    return [...sectors].sort((a, b) => {
      if (sortKey === "sector") return a.sector.localeCompare(b.sector) * (asc ? 1 : -1);
      const av = col?.value(a) ?? null;
      const bv = col?.value(b) ?? null;
      if (av === null) return 1;
      if (bv === null) return -1;
      return (av - bv) * (asc ? 1 : -1);
    });
  }, [sectors, sortKey, asc]);

  const sortBy = (key: string) => {
    if (key === sortKey) setAsc(!asc);
    else {
      setSortKey(key);
      setAsc(key === "sector");
    }
  };
  const aria = (key: string) => (key === sortKey ? (asc ? "ascending" : "descending") : "none");

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th aria-sort={aria("sector")}><button type="button" onClick={() => sortBy("sector")}>Sector</button></th>
            <th>Quadrant</th>
            {COLS.map((c) => (
              <th key={c.key} aria-sort={aria(c.key)} title={c.title}>
                <button type="button" onClick={() => sortBy(c.key)}>
                  {c.label}{c.key === sortKey ? (asc ? " ↑" : " ↓") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.sector} aria-selected={selected === s.sector} onClick={() => onSelect(s.sector)}
              tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") onSelect(s.sector); }}>
              <td className={styles.sectorCell}>
                {s.sector}
                {s.confidence === "low" && <span className={styles.muted} title="Low confidence reading"> · low conf.</span>}
              </td>
              <td>
                {s.quadrant ? (
                  <span className={styles.quadChip} style={{ color: QUADRANT_VAR[s.quadrant] }}>
                    <span className={styles.dot} style={{ background: QUADRANT_VAR[s.quadrant], marginRight: 0 }} />
                    {QUADRANT_LABEL[s.quadrant]}
                  </span>
                ) : <span className={styles.muted}>—</span>}
              </td>
              {COLS.map((c) => (
                <td key={c.key} style={{ background: tint(c, c.value(s)) }}>{c.show(s)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
