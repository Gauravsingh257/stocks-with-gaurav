"use client";

import { Settings2 } from "lucide-react";
import type { RiskMode, SetupType, StrategyMode } from "../_lib/opportunity";

export interface FilterState {
  strategy: StrategyMode | "all";
  setups: SetupType[];
  risk: RiskMode;
  direction: "all" | "BUY" | "SELL";
  query: string;
}

export const DEFAULT_FILTERS: FilterState = {
  strategy: "all",
  setups: [],
  risk: "conservative",
  direction: "all",
  query: "",
};

interface Props {
  value: FilterState;
  onChange: (next: FilterState) => void;
  total: number;
  visible: number;
}

const SETUP_OPTIONS: SetupType[] = ["A", "B", "C", "D"];

export default function AdvancedFilterBar({ value, onChange, total, visible }: Props) {
  const toggleSetup = (s: SetupType) => {
    const next = value.setups.includes(s) ? value.setups.filter((x) => x !== s) : [...value.setups, s];
    onChange({ ...value, setups: next });
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "12px 14px",
        background: "rgba(255,255,255,0.03)",
        border: "1px solid var(--border)",
        borderRadius: 14,
        backdropFilter: "blur(10px)",
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--text-secondary)", fontSize: "0.7rem", fontWeight: 600, letterSpacing: 0.6 }}>
        <Settings2 size={14} /> ADVANCED
      </div>

      <Group label="Strategy">
        <Pill active={value.strategy === "all"} onClick={() => onChange({ ...value, strategy: "all" })}>All</Pill>
        <Pill active={value.strategy === "intraday"} onClick={() => onChange({ ...value, strategy: "intraday" })}>Intraday</Pill>
        <Pill active={value.strategy === "swing"} onClick={() => onChange({ ...value, strategy: "swing" })}>Swing</Pill>
      </Group>

      <Group label="Setup">
        {SETUP_OPTIONS.map((s) => (
          <Pill key={s} active={value.setups.includes(s)} onClick={() => toggleSetup(s)}>
            {s}
          </Pill>
        ))}
      </Group>

      <Group label="Risk">
        <Pill active={value.risk === "conservative"} onClick={() => onChange({ ...value, risk: "conservative" })}>Conservative</Pill>
        <Pill active={value.risk === "aggressive"} onClick={() => onChange({ ...value, risk: "aggressive" })}>Aggressive</Pill>
      </Group>

      <Group label="Side">
        <Pill active={value.direction === "all"} onClick={() => onChange({ ...value, direction: "all" })}>All</Pill>
        <Pill active={value.direction === "BUY"} onClick={() => onChange({ ...value, direction: "BUY" })}>Long</Pill>
        <Pill active={value.direction === "SELL"} onClick={() => onChange({ ...value, direction: "SELL" })}>Short</Pill>
      </Group>

      <input
        type="search"
        value={value.query}
        onChange={(e) => onChange({ ...value, query: e.target.value })}
        placeholder="Search ticker…"
        style={{
          padding: "7px 10px",
          background: "rgba(255,255,255,0.04)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          color: "var(--text-primary)",
          fontSize: "0.74rem",
          width: 160,
          outline: "none",
        }}
      />

      <div style={{ marginLeft: "auto", fontSize: "0.7rem", color: "var(--text-dim)" }}>
        Showing <span style={{ color: "var(--accent)", fontWeight: 700 }}>{visible}</span> of {total}
      </div>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ fontSize: "0.6rem", color: "var(--text-dim)", letterSpacing: 0.6, textTransform: "uppercase" }}>{label}</span>
      <div style={{ display: "inline-flex", gap: 4 }}>{children}</div>
    </div>
  );
}

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: "5px 10px",
        borderRadius: 999,
        fontSize: "0.66rem",
        fontWeight: 700,
        cursor: "pointer",
        background: active ? "rgba(0,212,255,0.18)" : "rgba(255,255,255,0.03)",
        border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
        color: active ? "var(--accent)" : "var(--text-secondary)",
        letterSpacing: 0.4,
        transition: "all 0.15s",
      }}
    >
      {children}
    </button>
  );
}
