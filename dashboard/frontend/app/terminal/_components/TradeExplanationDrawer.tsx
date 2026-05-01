"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, X, AlertTriangle, XCircle, ArrowUpRight, ArrowDownRight, Layers, Activity, Target } from "lucide-react";
import MiniChart from "./MiniChart";
import type { Opportunity } from "../_lib/opportunity";
import { priceLabel, rrLabel } from "../_lib/opportunity";

interface Props {
  opp: Opportunity | null;
  onClose: () => void;
}

export default function TradeExplanationDrawer({ opp, onClose }: Props) {
  return (
    <AnimatePresence>
      {opp && (
        <>
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={onClose}
            style={{
              position: "fixed",
              inset: 0,
              background: "rgba(4,8,16,0.6)",
              backdropFilter: "blur(6px)",
              zIndex: 998,
            }}
          />
          <motion.aside
            key="drawer"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ duration: 0.32, ease: [0.21, 0.5, 0.3, 1] }}
            role="dialog"
            aria-label={`Trade setup for ${opp.symbol}`}
            style={{
              position: "fixed",
              top: 0,
              right: 0,
              bottom: 0,
              width: "min(560px, 100%)",
              background: "linear-gradient(180deg, #0d1626 0%, #080d1a 100%)",
              borderLeft: "1px solid var(--border)",
              boxShadow: "-20px 0 60px rgba(0,0,0,0.5)",
              zIndex: 999,
              overflowY: "auto",
              padding: 24,
            }}
          >
            <Header opp={opp} onClose={onClose} />
            <ChartBlock opp={opp} />
            <Section title="Why this trade?" icon={<Activity size={14} />}>
              <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.82rem", lineHeight: 1.6 }}>
                {opp.reasoning}
              </p>
            </Section>

            <LevelsBlock opp={opp} />

            <Section title="AI Score Breakdown" icon={<Layers size={14} />}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <ScoreRow label="Liquidity Sweep" status={opp.scores.liquidity ? "ok" : "fail"} detail={opp.signals.sweep} />
                <ScoreRow label="Structure" status={opp.scores.structure ? "ok" : "fail"} detail={opp.signals.structure} />
                <ScoreRow label="HTF Alignment" status={opp.scores.htf ? "ok" : "fail"} detail={opp.signals.htfBias} />
                <ScoreRow
                  label="Entry Quality"
                  status={opp.scores.entryQuality}
                  detail={`Confidence ${(opp.raw.confidence_score ?? 0).toFixed(0)} / 100`}
                />
              </div>
            </Section>

            <Section title="SMC Signals" icon={<Target size={14} />}>
              <SignalRow label="HTF Bias (1H / 4H)" value={opp.signals.htfBias} />
              <SignalRow label="Order Block Zone" value={opp.signals.orderBlock} />
              <SignalRow label="Fair Value Gap" value={opp.signals.fvg} />
              <SignalRow label="Liquidity Sweep" value={opp.signals.sweep} />
              <SignalRow label="Structure Shift" value={opp.signals.structure} />
            </Section>

            {Array.isArray(opp.raw.rejection_reason) && opp.raw.rejection_reason.length > 0 && (
              <Section title="Caveats" icon={<AlertTriangle size={14} />}>
                <ul style={{ margin: 0, paddingLeft: 16, color: "var(--text-secondary)", fontSize: "0.78rem", lineHeight: 1.6 }}>
                  {opp.raw.rejection_reason.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </Section>
            )}
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

function Header({ opp, onClose }: { opp: Opportunity; onClose: () => void }) {
  const isBuy = opp.direction === "BUY";
  const DirIcon = isBuy ? ArrowUpRight : ArrowDownRight;
  const dirColor = isBuy ? "#00e096" : "#ff4757";
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 18 }}>
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: "1.4rem", fontWeight: 800, color: "var(--text-primary)" }}>{opp.symbol}</h2>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "3px 10px",
              borderRadius: 999,
              background: `${dirColor}1f`,
              color: dirColor,
              border: `1px solid ${dirColor}55`,
              fontSize: "0.7rem",
              fontWeight: 700,
              letterSpacing: 0.5,
            }}
          >
            <DirIcon size={12} /> {opp.direction}
          </span>
        </div>
        <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", marginTop: 4, letterSpacing: 0.4 }}>
          Setup {opp.setup} · Grade {opp.grade} · {opp.status}
        </div>
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close"
        style={{
          background: "rgba(255,255,255,0.04)",
          border: "1px solid var(--border)",
          color: "var(--text-secondary)",
          padding: 6,
          borderRadius: 8,
          cursor: "pointer",
        }}
      >
        <X size={16} />
      </button>
    </div>
  );
}

function ChartBlock({ opp }: { opp: Opportunity }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.025)",
        border: "1px solid var(--border)",
        borderRadius: 14,
        padding: 14,
        marginBottom: 18,
        position: "relative",
      }}
    >
      <MiniChart data={opp.spark} direction={opp.direction} height={120} />
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: "0.66rem", color: "var(--text-dim)" }}>
        <span>Preview · 24 ticks</span>
        {opp.cmp != null && <span>CMP {priceLabel(opp.cmp)}</span>}
      </div>
    </div>
  );
}

function LevelsBlock({ opp }: { opp: Opportunity }) {
  const items = [
    { label: "Entry", value: priceLabel(opp.entry), color: "var(--accent)" },
    { label: "Stop Loss", value: priceLabel(opp.stop), color: "#ff4757" },
    { label: "Target", value: priceLabel(opp.target), color: "#00e096" },
    { label: "Risk : Reward", value: rrLabel(opp.rr), color: "var(--text-primary)" },
  ];
  return (
    <Section title="Plan" icon={<Target size={14} />}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
        {items.map((it) => (
          <div
            key={it.label}
            style={{
              background: "rgba(255,255,255,0.03)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: "10px 12px",
            }}
          >
            <div style={{ fontSize: "0.6rem", color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 0.6 }}>{it.label}</div>
            <div style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: "1.02rem", fontWeight: 700, color: it.color }}>{it.value}</div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section style={{ marginBottom: 18 }}>
      <h3 style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.7rem", color: "var(--text-secondary)", letterSpacing: 1.1, textTransform: "uppercase", margin: "0 0 8px" }}>
        {icon} {title}
      </h3>
      {children}
    </section>
  );
}

function ScoreRow({ label, status, detail }: { label: string; status: "ok" | "warn" | "fail"; detail: string }) {
  const map = {
    ok: { color: "#00e096", icon: <CheckCircle2 size={14} /> },
    warn: { color: "#ffa502", icon: <AlertTriangle size={14} /> },
    fail: { color: "#ff4757", icon: <XCircle size={14} /> },
  } as const;
  const m = map[status];
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.025)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: 10,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, color: m.color, fontWeight: 700, fontSize: "0.74rem" }}>
        {m.icon} {label}
      </div>
      <div style={{ marginTop: 4, fontSize: "0.68rem", color: "var(--text-dim)", lineHeight: 1.4 }}>{detail}</div>
    </div>
  );
}

function SignalRow({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 4px",
        borderBottom: "1px dashed var(--border)",
      }}
    >
      <span style={{ fontSize: "0.72rem", color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontSize: "0.74rem", color: "var(--text-primary)", fontWeight: 600 }}>{value}</span>
    </div>
  );
}
