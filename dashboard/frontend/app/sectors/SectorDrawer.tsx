"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, type SectorConstituentsResponse, type SectorRotationResponse, type SectorRotationRow } from "@/lib/api";
import {
  QUADRANT_HINT, QUADRANT_LABEL, QUADRANT_VAR, SOURCE_LABEL, crore, longDate, num, pct, signed, writtenIst,
} from "./format";
import styles from "./sectors.module.css";

const CONF_TEXT: Record<string, string> = {
  high: "High — at least 15 liquid stocks and at most half with provider-sourced sector labels.",
  medium: "Medium — at least 8 liquid stocks and at most 80% provider-sourced labels.",
  low: "Low — few liquid stocks or mostly provider-sourced labels. Treat the reading with caution.",
  insufficient: "Insufficient — fewer than 3 liquid stocks; not plotted.",
};

export default function SectorDrawer({ sector, data, onClose }: {
  sector: SectorRotationRow;
  data: SectorRotationResponse;
  onClose: () => void;
}) {
  // Keyed on the sector by the parent, so this state starts fresh per sector.
  const [members, setMembers] = useState<SectorConstituentsResponse | null>(null);
  const [membersError, setMembersError] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.sectorConstituents(sector.sector)
      .then((d) => { if (!cancelled) setMembers(d); })
      .catch(() => { if (!cancelled) setMembersError(true); });
    return () => { cancelled = true; };
  }, [sector.sector]);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const q = sector.quadrant;
  const bench = data.benchmark;
  const liquid = members?.items.filter((i) => i.liquid) ?? [];
  const listed = members ? (showAll ? members.items : members.items.slice(0, 25)) : [];
  const defs = data.definitions;

  const metrics: { label: string; value: string; note: string }[] = [
    { label: `Relative strength ${bench.short}`, value: num(sector.rs_ratio, 2), note: defs.rs_ratio },
    { label: "Momentum", value: num(sector.rs_momentum, 2), note: defs.rs_momentum },
    { label: "Acceleration", value: signed(sector.rs_accel), note: defs.rs_accel },
    { label: `Return, last ${data.lookback_label}`, value: `${signed(sector.ret_lookback, 1)}%`,
      note: bench.key === "market" ? `${signed(sector.ret_vs_market ?? null, 1)} pts vs the whole market`
        : `Equal-weight sector return` },
    { label: "Breadth >20 / 50 / 200D", value: `${pct(sector.pct_above_20)} · ${pct(sector.pct_above_50)} · ${pct(sector.pct_above_200)}`,
      note: defs.pct_above },
    { label: "52-week highs / lows", value: `${sector.new_highs_52w ?? "—"} / ${sector.new_lows_52w ?? "—"}`,
      note: `Of ${sector.n_52w_eligible ?? "—"} stocks with a full year of prices.` },
    { label: "Volume participation", value: num(sector.vol_ratio, 2), note: defs.vol_ratio },
    { label: "Up-day traded value", value: pct(sector.up_turnover_pct), note: defs.up_turnover_pct },
    { label: "Dispersion", value: num(sector.dispersion, 1), note: defs.dispersion },
  ];
  if (bench.key === "nifty50" && sector.rs_ratio_core !== null) {
    metrics.push({ label: "Rel. strength, NSE/manual labels only", value: num(sector.rs_ratio_core, 2),
      note: "The same reading using only stocks whose sector comes from NSE or a manual assignment." });
  }

  // Portalled to <body>: the dashboard shell wraps page content in a z-[2]
  // stacking context, which would otherwise keep the drawer under the fixed
  // mobile navigation (z-50) whatever z-index the drawer declares.
  return createPortal(
    <div className={styles.page}>
      <div className={styles.overlay} onClick={onClose} aria-hidden="true" />
      <aside className={styles.drawer} role="dialog" aria-modal="true" aria-labelledby="sector-drawer-title">
        <div className={styles.drawerHead}>
          <div>
            <h2 id="sector-drawer-title" className={styles.drawerTitle}>{sector.sector}</h2>
            {q && (
              <div className={styles.quadChip} style={{ color: QUADRANT_VAR[q], marginTop: 6 }}>
                <span className={styles.dot} style={{ background: QUADRANT_VAR[q], marginRight: 0 }} />
                {QUADRANT_LABEL[q]} {bench.short}
              </div>
            )}
          </div>
          <button ref={closeRef} type="button" className={styles.close} onClick={onClose}>Close</button>
        </div>

        {q && <div className={styles.metaLine}>{QUADRANT_HINT[q]}</div>}
        <div className={styles.metaLine}>
          <b>Benchmark:</b> {bench.label}. {bench.description}
          <br />
          <b>Data:</b> {data.timeframe_label.toLowerCase()} bars to the close on {longDate(sector.as_of)}
          {sector.partial ? " (week in progress)" : ""} · computed {writtenIst(sector.written_at)}
        </div>

        <div className={styles.metricGrid}>
          {metrics.map((mt) => (
            <div key={mt.label} className={styles.metric}>
              <span>{mt.label}</span>
              <b>{mt.value}</b>
              <small>{mt.note}</small>
            </div>
          ))}
        </div>

        <div className={styles.confBox}>
          <b>Confidence: {sector.confidence ?? "—"}.</b> {CONF_TEXT[sector.confidence ?? ""] ?? ""}{" "}
          {sector.n_constituents ?? "—"} liquid stocks; {pct((sector.provider_share ?? 0) * 100)} with
          provider-sourced sector labels. {defs.provider_share}
        </div>

        <div>
          <h3 className={styles.sectionTitle}>
            Stocks in {sector.sector}{members ? ` · ${members.count}` : ""}
          </h3>
          {!members && !membersError && <div className={styles.skeleton} style={{ height: 120 }} />}
          {membersError && <div className={styles.muted}>The stock list could not be loaded.</div>}
          {members && (
            <>
              <div className={styles.metaLine} style={{ marginBottom: 6 }}>
                {liquid.length} liquid · sorted by traded value · weekly figures from{" "}
                {longDate(members.refreshed_at)}
              </div>
              <div className={styles.stockList}>
                {listed.map((s) => (
                  <Link key={s.symbol} href={`/stock/${encodeURIComponent(s.symbol)}`} className={styles.stock}>
                    <span style={{ minWidth: 0 }}>
                      <span className={styles.stockSym}>{s.symbol}</span>
                      {!s.liquid && <span className={styles.muted}> · illiquid</span>}
                      <span className={styles.stockName}>
                        {s.company_name || "—"} · {SOURCE_LABEL[s.sector_source ?? ""] ?? "Unlabelled source"}
                      </span>
                    </span>
                    <span className={styles.muted} title="Average traded value per day">{crore(s.turnover_cr)}</span>
                    <span title="One-year return">{s.ret_1y_pct === null ? "—" : `${signed(s.ret_1y_pct, 1)}%`}</span>
                  </Link>
                ))}
              </div>
              {members.items.length > 25 && (
                <button type="button" className={styles.linkBtn} onClick={() => setShowAll(!showAll)}>
                  {showAll ? "Show fewer" : `Show all ${members.items.length}`}
                </button>
              )}
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}
