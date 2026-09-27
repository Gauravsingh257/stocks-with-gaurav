"use client";
/**
 * Sector Rotation view (Phase 2). Reads /api/sectors/rotation — precomputed
 * Phase 1 rows; nothing is calculated here beyond layout. Analytics only: the
 * copy describes measured relative strength and never suggests an action.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, type SectorBenchmarkKey, type SectorQuadrant, type SectorRotationResponse, type SectorTimeframe,
} from "@/lib/api";
import RotationMap from "./RotationMap";
import SectorDrawer from "./SectorDrawer";
import SectorHeatmap from "./SectorHeatmap";
import { QUADRANT_HINT, QUADRANT_LABEL, QUADRANT_ORDER, QUADRANT_VAR, longDate, writtenIst } from "./format";
import styles from "./sectors.module.css";

type LoadState = "loading" | "ready" | "error" | "disabled";

const TRAILS = [5, 10, 20];

const DEF_LABEL: Record<string, string> = {
  rs_ratio: "Relative strength",
  rs_momentum: "Momentum",
  rs_accel: "Acceleration",
  pct_above: "Breadth",
  new_highs_52w: "52-week highs / lows",
  vol_ratio: "Volume participation",
  up_turnover_pct: "Up-day traded value",
  dispersion: "Dispersion",
  confidence: "Confidence",
  provider_share: "Provider share",
};

function Segmented<T extends string | number>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className={styles.controlGroup}>
      <span className={styles.controlLabel}>{label}</span>
      <div className={styles.seg} role="group" aria-label={label}>
        {options.map((o) => (
          <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function SectorRotation() {
  const [tf, setTf] = useState<SectorTimeframe>("D");
  const [bench, setBench] = useState<SectorBenchmarkKey>("market");
  const [trail, setTrail] = useState(10);
  const [data, setData] = useState<SectorRotationResponse | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ key: string; disabled: boolean } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // Loading / refreshing are derived from which request last settled, so the
  // effect only sets state from the async callbacks.
  const reqKey = `${tf}|${bench}|${trail}|${attempt}`;
  useEffect(() => {
    let cancelled = false;
    api.sectorRotation(tf, bench, trail)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoadedKey(reqKey);
        setFailure(null);
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setFailure({ key: reqKey, disabled: /→ 404/.test(err.message) });
      });
    return () => { cancelled = true; };
  }, [tf, bench, trail, reqKey]);

  const failedNow = failure?.key === reqKey;
  const refreshing = loadedKey !== reqKey && !failedNow;
  const state: LoadState = failedNow ? (failure.disabled ? "disabled" : "error")
    : data ? "ready" : "loading";

  const select = useCallback((sector: string) => {
    setSelected(sector);
    setDrawerOpen(true);
  }, []);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);

  const sectors = useMemo(() => data?.sectors ?? [], [data]);
  const plottable = useMemo(() => sectors.filter((s) => s.plottable), [sectors]);
  const excluded = sectors.length - plottable.length;
  const current = sectors.find((s) => s.sector === selected) ?? null;
  const unit = tf === "D" ? "session" : "week";

  const header = (
    <section className={`${styles.card} ${styles.header}`}>
      <div className={styles.titleRow}>
        <div>
          <h1 className={styles.title}>Sector Rotation</h1>
          <p className={styles.subtitle}>
            Relative strength, momentum and breadth for NSE sectors, built from every liquid stock in the
            universe. A measurement of how sectors have been moving, not a forecast or a recommendation.
          </p>
        </div>
        <span className={styles.previewTag}>Preview</span>
      </div>
      <div className={styles.controls}>
        <Segmented label="Timeframe" value={tf} onChange={setTf}
          options={[{ value: "D", label: "Daily" }, { value: "W", label: "Weekly" }]} />
        <Segmented label="Benchmark" value={bench} onChange={setBench}
          options={[{ value: "market", label: "Whole market" }, { value: "nifty50", label: "NIFTY 50" }]} />
        <Segmented label="Trail" value={trail} onChange={setTrail}
          options={TRAILS.map((t) => ({ value: t, label: String(t) }))} />
        {refreshing && data && <span className={styles.muted} style={{ fontSize: "0.78rem" }}>Updating…</span>}
      </div>
    </section>
  );

  if (state === "loading" && !data) {
    return (
      <div className={styles.page} aria-busy="true">
        {header}
        <div className={styles.mainGrid}>
          <div className={`${styles.card} ${styles.skeleton}`} style={{ height: 420 }} />
          <div className={`${styles.card} ${styles.skeleton}`} style={{ height: 420 }} />
        </div>
        <div className={`${styles.card} ${styles.skeleton}`} style={{ height: 260 }} />
      </div>
    );
  }

  if (state === "disabled") {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.card}>
          <div className={styles.errorBox}>Sector rotation is not available right now.</div>
        </div>
      </div>
    );
  }

  if (state === "error" && !data) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.card} role="alert">
          <div className={styles.errorBox}>
            <span>The sector data could not be loaded. The server may be restarting.</span>
            <button type="button" className={styles.retry} onClick={() => setAttempt((a) => a + 1)}>
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!data || sectors.length === 0) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.card}>
          <div className={styles.errorBox}>No sector readings have been computed yet.</div>
        </div>
      </div>
    );
  }

  const f = data.freshness;
  return (
    <div className={styles.page}>
      {header}

      <div className={styles.benchBanner} aria-live="polite">
        <span>Quadrants measured against <strong>{data.benchmark.label}</strong>.</span>
        <span>{data.benchmark.description}</span>
      </div>

      <div className={styles.status}>
        <span>
          <span className={styles.dot} style={{ background: f.status === "fresh" ? "var(--success)" : "var(--warning)" }} />
          {data.timeframe_label} · as of the {longDate(data.as_of)} close
          {data.week_partial ? " (week in progress)" : ""}
        </span>
        <span>Computed {writtenIst(data.written_at)}</span>
        <span>{plottable.length} sectors plotted{excluded ? ` · ${excluded} with too few liquid stocks` : ""}</span>
        {state === "error" && <span style={{ color: "var(--warning)" }}>Last update failed — showing the previous reading.</span>}
      </div>

      {f.status === "stale" && <div className={styles.stale} role="status">{f.note}</div>}

      <div className={styles.mainGrid}>
        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>Rotation map · {data.benchmark.short}</h2>
          <RotationMap sectors={sectors} selected={drawerOpen ? selected : null} onSelect={select}
            benchmarkShort={data.benchmark.short} unitLabel={unit} />
          <div className={styles.mapNote}>
            <div className={styles.legend}>
              {QUADRANT_ORDER.map((q) => (
                <span key={q}><span className={styles.dot} style={{ background: QUADRANT_VAR[q], marginRight: 0 }} />{QUADRANT_LABEL[q]}</span>
              ))}
            </div>
            <span>Each dot = 1 {unit} · {data.trail_length}-{unit} trail · hollow = low confidence</span>
          </div>
        </section>

        <section className={styles.card}>
          <h2 className={styles.sectionTitle}>Quadrants · {data.benchmark.short}</h2>
          <div className={styles.quadGrid}>
            {QUADRANT_ORDER.map((q: SectorQuadrant) => {
              const list = plottable
                .filter((s) => s.quadrant === q)
                .sort((a, b) => (b.rs_ratio ?? 0) - (a.rs_ratio ?? 0));
              return (
                <div key={q} className={styles.quad}>
                  <div className={styles.quadHead} style={{ color: QUADRANT_VAR[q] }}>
                    <span>{QUADRANT_LABEL[q]}</span><span className={styles.muted}>{list.length}</span>
                  </div>
                  <p className={styles.quadHint}>{QUADRANT_HINT[q]}</p>
                  <div className={styles.chips}>
                    {list.length === 0 && <span className={styles.none}>None</span>}
                    {list.map((s) => (
                      <button key={s.sector} type="button"
                        className={`${styles.chip} ${s.confidence === "low" ? styles.chipLow : ""}`}
                        aria-pressed={drawerOpen && selected === s.sector} onClick={() => select(s.sector)}>
                        {s.sector}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      <section className={styles.card}>
        <h2 className={styles.sectionTitle}>Sector metrics · {data.timeframe_label.toLowerCase()} · {data.benchmark.short}</h2>
        <SectorHeatmap sectors={sectors} selected={drawerOpen ? selected : null} onSelect={select} />
      </section>

      <footer className={`${styles.card} ${styles.footer}`}>
        <div className={styles.defs}>
          {Object.entries(data.definitions).map(([k, v]) => (
            <div key={k}><b>{DEF_LABEL[k] ?? k.replace(/_/g, " ")}:</b> {v}</div>
          ))}
        </div>
        <div>{data.disclaimer}</div>
      </footer>

      {drawerOpen && current && (
        <SectorDrawer key={current.sector} sector={current} data={data} onClose={closeDrawer} />
      )}
    </div>
  );
}
