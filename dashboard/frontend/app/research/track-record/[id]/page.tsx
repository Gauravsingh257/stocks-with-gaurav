"use client";

/**
 * Trade Detail — the full life of one trade.
 *
 * Everything shown is drawn from the recorded lifecycle. The price path plots
 * the four points we actually stored (entry, worst, best, exit) rather than a
 * synthetic candle series: inventing intermediate bars would present fabricated
 * data as history. The post-trade read is derived from the record for the same
 * reason — it describes how the trade was managed and what that cost, and does
 * not speculate about why price moved.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, MessageSquare, Activity, Brain, GitBranch } from "lucide-react";
import { api, LifecycleDetail } from "@/lib/api";

const label = (s: string) =>
  (s || "").replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

function statusColor(s: string) {
  if (s === "TARGET_HIT") return "#00e096";
  if (s === "STOP_HIT") return "#ff4757";
  if (["ACTIVE", "ENTRY_TRIGGERED", "PARTIAL_EXIT", "TRAILING_SL", "BREAKEVEN"].includes(s)) return "#00d4ff";
  if (s === "AWAITING_ENTRY") return "#f0c060";
  return "#8b8b9a";
}

function Section({ icon, title, children }: {
  icon: React.ReactNode; title: string; children: React.ReactNode;
}) {
  return (
    <div className="glass" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        {icon}
        <h2 style={{ margin: 0, fontSize: "0.9rem" }}>{title}</h2>
      </div>
      {children}
    </div>
  );
}

/** Excursion bar: where price actually travelled between stop and target. */
function PricePath({ d }: { d: NonNullable<LifecycleDetail["price_path"]> }) {
  if (!d?.available || !d.points?.length) {
    return <div style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>No price path recorded.</div>;
  }
  const prices = d.points.map((p) => p.price).filter((n) => typeof n === "number");
  const extras = [d.stop_loss, d.target_1, d.target_2].filter((n): n is number => typeof n === "number");
  const lo = Math.min(...prices, ...extras);
  const hi = Math.max(...prices, ...extras);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;

  return (
    <div>
      <div style={{ position: "relative", height: 58, marginBottom: 14 }}>
        <div style={{ position: "absolute", top: 26, left: 0, right: 0, height: 3, background: "rgba(255,255,255,0.08)", borderRadius: 2 }} />
        {typeof d.stop_loss === "number" && (
          <div style={{ position: "absolute", left: `${pos(d.stop_loss)}%`, top: 14, width: 2, height: 26, background: "#ff4757" }} title={`Stop ₹${d.stop_loss}`} />
        )}
        {typeof d.target_1 === "number" && (
          <div style={{ position: "absolute", left: `${pos(d.target_1)}%`, top: 14, width: 2, height: 26, background: "#00e096" }} title={`Target ₹${d.target_1}`} />
        )}
        {d.points.map((p, i) => (
          <div key={i} style={{ position: "absolute", left: `${pos(p.price)}%`, top: 20, transform: "translateX(-50%)", textAlign: "center" }}>
            <div style={{ width: 11, height: 11, borderRadius: 999, margin: "0 auto",
                          background: p.label === "Exit" ? "#00d4ff" : p.label.includes("Best") ? "#00e096"
                                    : p.label.includes("Worst") ? "#ff4757" : "#b9b9c6" }} />
            <div style={{ fontSize: "0.56rem", color: "var(--text-secondary)", marginTop: 3, whiteSpace: "nowrap" }}>{p.label}</div>
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 8, fontSize: "0.72rem" }}>
        {d.points.map((p, i) => (
          <div key={i}>
            <div style={{ color: "var(--text-secondary)", fontSize: "0.6rem" }}>{p.label}</div>
            <div style={{ fontWeight: 600 }}>₹{p.price}
              {p.pct != null && <span style={{ marginLeft: 5, color: p.pct >= 0 ? "#00e096" : "#ff4757" }}>
                {p.pct > 0 ? "+" : ""}{p.pct}%</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function TradeDetailPage() {
  const params = useParams();
  const id = String(params?.id ?? "");
  const [d, setD] = useState<LifecycleDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    api.lifecycleDetail(id).then(setD).catch(() => setD(null)).finally(() => setLoading(false));
  }, [id]);

  if (loading) return <div style={{ padding: 30, color: "var(--text-secondary)" }}>Loading…</div>;
  if (!d?.found || !d.trade) {
    return (
      <div style={{ padding: 30 }}>
        <Link href="/research/track-record" style={{ color: "#5b9cf6", fontSize: "0.82rem" }}>← Track Record</Link>
        <p style={{ color: "var(--text-secondary)", marginTop: 14 }}>Trade not found.</p>
      </div>
    );
  }

  const t = d.trade;
  const col = statusColor(t.status);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/research/track-record" style={{ display: "flex", alignItems: "center", gap: 6, color: "#5b9cf6", textDecoration: "none", fontSize: "0.82rem" }}>
          <ArrowLeft size={16} /> Track Record
        </Link>
        <div style={{ width: 1, height: 20, background: "rgba(255,255,255,0.1)" }} />
        <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>{t.symbol.replace("NSE:", "")}</h1>
        <span style={{ fontSize: "0.68rem", padding: "3px 10px", borderRadius: 999, background: `${col}22`, color: col }}>
          {label(t.status)}
        </span>
        <span style={{ fontSize: "0.66rem", padding: "2px 8px", borderRadius: 4, background: "rgba(255,255,255,0.06)", color: "var(--text-secondary)" }}>
          {t.portfolio ?? t.source}{t.engine_version ? ` · ${t.engine_version}` : ""}
        </span>
      </div>

      <div className="glass" style={{ padding: 16, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: 12 }}>
        {[["Entry", t.entry_price != null ? `₹${t.entry_price}` : "—"],
          ["Stop", t.stop_loss != null ? `₹${t.stop_loss}` : "—"],
          ["Target", t.target_1 != null ? `₹${t.target_1}` : "—"],
          ["Exit", t.exit_price != null ? `₹${t.exit_price}` : "—"],
          ["P&L", t.pnl_pct != null ? `${t.pnl_pct > 0 ? "+" : ""}${t.pnl_pct}%` : "—"],
          ["RR", t.rr_realized != null ? `${t.rr_realized}R` : "—"],
          ["Held", t.holding_days != null ? `${t.holding_days}d` : "—"],
          ["Best (MFE)", t.mfe_pct != null ? `+${t.mfe_pct}%` : "—"],
          ["Worst (MAE)", t.mae_pct != null ? `${t.mae_pct}%` : "—"],
          ["Confidence", t.confidence != null ? Math.round(t.confidence) : "—"]].map(([k, v]) => (
          <div key={String(k)}>
            <div style={{ color: "var(--text-secondary)", fontSize: "0.62rem" }}>{k}</div>
            <div style={{ fontWeight: 700, fontSize: "0.9rem",
                          color: k === "P&L" && t.pnl_pct != null ? (t.pnl_pct >= 0 ? "#00e096" : "#ff4757") : undefined }}>{v}</div>
          </div>
        ))}
      </div>

      <Section icon={<Activity size={15} color="#00d4ff" />} title="Price path">
        <PricePath d={d.price_path ?? { available: false }} />
      </Section>

      {d.analysis && (
        <Section icon={<Brain size={15} color="#c084fc" />} title="Post-trade analysis">
          <div style={{ fontSize: "0.85rem", fontWeight: 700, marginBottom: 8, color: col }}>{d.analysis.verdict}</div>
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 5 }}>
            {d.analysis.notes.map((n, i) => (
              <li key={i} style={{ fontSize: "0.76rem", lineHeight: 1.5 }}>{n}</li>
            ))}
          </ul>
          {d.analysis.basis && (
            <p style={{ fontSize: "0.64rem", color: "var(--text-secondary)", marginTop: 10, marginBottom: 0 }}>
              {d.analysis.basis}
            </p>
          )}
        </Section>
      )}

      <Section icon={<Activity size={15} color="#00e096" />} title="Lifecycle">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(d.events ?? []).map((e, i) => (
            <div key={i} style={{ display: "flex", gap: 12, fontSize: "0.74rem", borderLeft: `2px solid ${statusColor(e.to_status ?? "")}`, paddingLeft: 12 }}>
              <span style={{ color: "var(--text-secondary)", minWidth: 138, fontSize: "0.68rem" }}>
                {(e.occurred_at ?? "").slice(0, 19).replace("T", " ")}
              </span>
              <span>
                <strong>{label(e.event)}</strong>
                {e.from_status && e.to_status && e.from_status !== e.to_status &&
                  ` · ${label(e.from_status)} → ${label(e.to_status)}`}
                {!e.from_status && e.to_status && ` · ${label(e.to_status)}`}
                {e.price != null && ` · ₹${e.price}`}
                {e.note && ` · ${e.note}`}
              </span>
            </div>
          ))}
          {(d.events ?? []).length === 0 && (
            <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>No events recorded.</div>
          )}
        </div>
      </Section>

      {d.chain && d.chain.stages.length > 1 && (
        <Section icon={<GitBranch size={15} color="#f0c060" />} title="Idea chain">
          <p style={{ fontSize: "0.72rem", color: "var(--text-secondary)", marginTop: 0 }}>
            {d.chain.converted
              ? `Traded by ${d.chain.engines_that_traded_it.join(", ")}.`
              : "Published but not taken by any book."}
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {d.chain.stages.map((s) => (
              <Link key={s.uuid} href={`/research/track-record/${s.uuid}`}
                    style={{ display: "flex", gap: 12, alignItems: "center", fontSize: "0.74rem",
                             textDecoration: "none", color: "inherit",
                             padding: "6px 10px", borderRadius: 6,
                             background: s.uuid === t.uuid ? "rgba(0,212,255,0.08)" : "transparent" }}>
                <span style={{ fontSize: "0.6rem", padding: "2px 7px", borderRadius: 4, background: "rgba(255,255,255,0.06)" }}>
                  {s.stage ?? "—"}
                </span>
                <span style={{ minWidth: 96 }}>{s.portfolio ?? s.source}</span>
                <span style={{ color: statusColor(s.status) }}>{label(s.status)}</span>
                {s.pnl_pct != null && (
                  <span style={{ color: s.pnl_pct >= 0 ? "#00e096" : "#ff4757" }}>
                    {s.pnl_pct > 0 ? "+" : ""}{s.pnl_pct}%
                  </span>
                )}
              </Link>
            ))}
          </div>
        </Section>
      )}

      {d.recommendation && (
        <Section icon={<Brain size={15} color="#5b9cf6" />} title="Original recommendation">
          <pre style={{ fontSize: "0.68rem", background: "rgba(0,0,0,0.25)", padding: 12, borderRadius: 6, overflowX: "auto", margin: 0 }}>
            {JSON.stringify(d.recommendation, null, 2)}
          </pre>
        </Section>
      )}

      {d.context && (
        <Section icon={<Activity size={15} color="#b9b9c6" />} title="Market context at entry">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 10, fontSize: "0.74rem" }}>
            {Object.entries(d.context).map(([k, v]) => (
              <div key={k}>
                <div style={{ color: "var(--text-secondary)", fontSize: "0.62rem" }}>{label(k)}</div>
                <div style={{ fontWeight: 600, wordBreak: "break-word" }}>{v == null ? "—" : String(v)}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section icon={<MessageSquare size={15} color="#00d4ff" />} title="Alerts sent">
        {(d.alerts ?? []).length === 0 ? (
          <div style={{ fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            No alerts recorded for this symbol. Capture began when alert logging was added, so older trades have none.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {(d.alerts ?? []).map((a, i) => (
              <div key={i} style={{ background: "rgba(255,255,255,0.03)", padding: 10, borderRadius: 6 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                  <span style={{ fontSize: "0.6rem", padding: "2px 7px", borderRadius: 4, background: "rgba(0,212,255,0.12)", color: "#00d4ff" }}>
                    {label(a.kind)}
                  </span>
                  <span style={{ fontSize: "0.64rem", color: "var(--text-secondary)" }}>
                    {(a.sent_at ?? "").slice(0, 19).replace("T", " ")}
                  </span>
                </div>
                <pre style={{ margin: 0, fontSize: "0.7rem", whiteSpace: "pre-wrap", fontFamily: "inherit" }}>
                  {a.message.replace(/<[^>]+>/g, "")}
                </pre>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
