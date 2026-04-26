"use client";

import Link from "next/link";
import { ArrowRight, BarChart2, Bookmark, ShieldCheck, Sparkles, Target } from "lucide-react";
import type { ResearchAggregatePerformance, ResearchCoverageResponse } from "@/lib/api";
import type { AuthUser } from "@/lib/auth";

function fmtPct(v?: number | null) {
  if (v == null) return "-";
  return `${v.toFixed(1)}%`;
}

export function ResearchConversionPanel({
  perf,
  coverage,
  user,
}: {
  perf: ResearchAggregatePerformance | null;
  coverage: ResearchCoverageResponse | null;
  user: AuthUser | null;
}) {
  const totalIdeas = perf?.total_recommendations ?? 0;
  const hitRate = perf?.hit_rate_pct ?? null;
  const scanned = coverage?.latest?.SWING?.universe_scanned ?? coverage?.latest?.LONGTERM?.universe_scanned ?? 0;
  const available = coverage?.available_universe ?? 0;
  const trackRecordLabel =
    totalIdeas === 0
      ? "No closed picks yet"
      : `${totalIdeas} picks · ${fmtPct(hitRate)} hit rate`;

  return (
    <section
      className="glass"
      style={{
        padding: "20px 22px",
        background:
          "radial-gradient(circle at 12% 10%, rgba(0,212,255,0.12), transparent 32%), linear-gradient(135deg, rgba(15,23,42,0.88), rgba(2,6,23,0.76))",
        border: "1px solid rgba(0,212,255,0.16)",
      }}
    >
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(280px, 0.85fr)", gap: 18, alignItems: "center" }}>
        <div>
          <div style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 999, background: "rgba(0,224,150,0.12)", color: "var(--success)", border: "1px solid rgba(0,224,150,0.25)", fontSize: "0.72rem", fontWeight: 800, marginBottom: 10 }}>
            <Sparkles size={13} /> Actionable AI Research
          </div>
          <h2 style={{ margin: "0 0 8px", fontSize: "clamp(1.45rem, 3vw, 2.25rem)", lineHeight: 1.08, fontWeight: 900 }}>
            Discovery to Watchlist to Final Review.
          </h2>
          <p style={{ margin: "0 0 14px", color: "var(--text-secondary)", maxWidth: 720, lineHeight: 1.6, fontSize: "0.92rem" }}>
            Every stock must earn its stage through SMC evidence, risk levels, and confidence scoring. This is a research pipeline, not random stock suggestions.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, maxWidth: 680 }}>
            <StagePill label="1. Discovery" value="Track only" />
            <StagePill label="2. Watchlist" value="Add alert" />
            <StagePill label="3. Final" value="Ready setup" />
            <StagePill label="4. Review" value="Manual decision" />
          </div>
        </div>

        <div style={{ display: "grid", gap: 10 }}>
          <TrustRow icon={<Target size={15} />} label="Research output" value="Entry · SL · Target · R:R" />
          <TrustRow icon={<ShieldCheck size={15} />} label="Track record" value={trackRecordLabel} />
          <TrustRow icon={<BarChart2 size={15} />} label="Market coverage" value={available ? `${scanned}/${available} NSE stocks scanned` : "Universe scan ready"} />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 2 }}>
            <Link
              href="/research/track-record"
              style={{
                textDecoration: "none",
                textAlign: "center",
                padding: "9px 10px",
                borderRadius: 8,
                border: "1px solid rgba(0,224,150,0.3)",
                background: "rgba(0,224,150,0.1)",
                color: "var(--success)",
                fontWeight: 800,
                fontSize: "0.78rem",
              }}
            >
              Verify Results <ArrowRight size={12} style={{ display: "inline", marginLeft: 4 }} />
            </Link>
            <Link
              href={user ? "/watchlist" : "/login"}
              style={{
                textDecoration: "none",
                textAlign: "center",
                padding: "9px 10px",
                borderRadius: 8,
                border: "1px solid rgba(245,158,11,0.3)",
                background: "rgba(245,158,11,0.1)",
                color: "var(--warning)",
                fontWeight: 800,
                fontSize: "0.78rem",
              }}
            >
              <Bookmark size={12} style={{ display: "inline", marginRight: 4 }} />
              {user ? "Open Watchlist" : "Save Watchlist"}
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

function StagePill({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ padding: "8px 10px", borderRadius: 8, border: "1px solid rgba(91,156,246,0.22)", background: "rgba(91,156,246,0.08)" }}>
      <div style={{ color: "var(--text-primary)", fontSize: "0.76rem", fontWeight: 850 }}>{label}</div>
      <div style={{ color: "var(--text-secondary)", fontSize: "0.68rem", fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function TrustRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 10, border: "1px solid var(--border)", background: "rgba(255,255,255,0.035)" }}>
      <span style={{ display: "grid", placeItems: "center", width: 28, height: 28, borderRadius: 8, background: "rgba(0,212,255,0.1)", color: "var(--accent)" }}>
        {icon}
      </span>
      <div>
        <div style={{ color: "var(--text-dim)", fontSize: "0.64rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
        <div style={{ color: "var(--text-primary)", fontSize: "0.82rem", fontWeight: 800 }}>{value}</div>
      </div>
    </div>
  );
}
