"use client";

/**
 * WatchlistMonitor — the active pre-portfolio staging area.
 *
 * Each card shows: name, pattern/tag, entry zone, SL, T1/T2, live CMP,
 * distance-from-entry, a status badge (WAITING/APPROACHING/ACTIONABLE/MISSED/
 * ARMED/TRIGGERED), and the actions: Buy CMP · Set Entry Alert · Ignore/Remove · Study.
 * Triggered/bought ideas flow into the per-user portfolio (user_positions),
 * tracked by the shared engine — this component never opens a position itself
 * beyond calling the API.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Crosshair, Plus, RefreshCw, ShoppingCart, Trash2, X, ExternalLink, Target } from "lucide-react";
import { api, type WatchlistMonitorItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function clean(s: string): string {
  return s.replace(/^NSE:/i, "").trim().toUpperCase();
}
function inr(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

const BADGE: Record<string, { label: string; fg: string; bg: string; bd: string }> = {
  WAITING:     { label: "Waiting",     fg: "#7dd3fc", bg: "rgba(125,211,252,0.12)", bd: "rgba(125,211,252,0.4)" },
  APPROACHING: { label: "Approaching", fg: "#fbbf24", bg: "rgba(251,191,36,0.12)",  bd: "rgba(251,191,36,0.4)" },
  ACTIONABLE:  { label: "Actionable",  fg: "#34d399", bg: "rgba(16,185,129,0.16)",  bd: "rgba(16,185,129,0.45)" },
  MISSED:      { label: "Missed",      fg: "#fda4af", bg: "rgba(244,63,94,0.12)",   bd: "rgba(244,63,94,0.4)" },
  ARMED:       { label: "Alert Set",   fg: "#a78bfa", bg: "rgba(139,92,246,0.14)",  bd: "rgba(139,92,246,0.45)" },
  TRIGGERED:   { label: "Triggered",   fg: "#f472b6", bg: "rgba(244,114,182,0.14)", bd: "rgba(244,114,182,0.5)" },
};

function StatusBadge({ status }: { status: string }) {
  const b = BADGE[status] || BADGE.WAITING;
  return (
    <span style={{ fontSize: "0.66rem", fontWeight: 800, padding: "3px 9px", borderRadius: 999,
      color: b.fg, background: b.bg, border: `1px solid ${b.bd}`, textTransform: "uppercase", letterSpacing: 0.4 }}>
      {b.label}
    </span>
  );
}

export default function WatchlistMonitor() {
  const { token } = useAuth();
  const [items, setItems] = useState<WatchlistMonitorItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) { setLoading(false); return; }
    try {
      const res = await api.watchlistMonitorList(token);
      setItems(res.items || []);
    } catch { /* keep prior */ } finally { setLoading(false); }
  }, [token]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  const act = useCallback(async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id); setErr(null);
    try { await fn(); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "action failed"); }
    finally { setBusy(null); }
  }, [load]);

  if (!token) {
    return <div style={{ padding: 14, color: "var(--text-secondary)", fontSize: "0.82rem" }}>Sign in to use the active watchlist.</div>;
  }

  return (
    <section className="glass" style={{ padding: 16, borderRadius: 14, border: "1px solid rgba(139,92,246,0.28)", display: "grid", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: "1.05rem", fontWeight: 800, display: "flex", alignItems: "center", gap: 8 }}>
            <Crosshair size={18} color="#a78bfa" /> Active Watchlist
          </h2>
          <p style={{ margin: "2px 0 0", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            Monitors entry zones · arm a trigger or buy at CMP · fills flow into your portfolio
          </p>
        </div>
        <div style={{ display: "inline-flex", gap: 8 }}>
          <button onClick={load} title="Refresh" style={btn("ghost")}><RefreshCw size={13} /> </button>
          <button onClick={() => setShowAdd(true)} style={btn("primary")}><Plus size={14} /> Add</button>
        </div>
      </div>

      {err && <div style={{ fontSize: "0.74rem", color: "#fda4af" }}>{err}</div>}

      {loading ? (
        <div style={{ padding: 12, color: "var(--text-secondary)", fontSize: "0.8rem" }}>Loading…</div>
      ) : items.length === 0 ? (
        <div style={{ padding: 16, color: "var(--text-secondary)", fontSize: "0.82rem", textAlign: "center", border: "1px dashed var(--border)", borderRadius: 10 }}>
          Nothing staged yet. Add a stock with its entry zone, or push ideas here from Research.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))", gap: 12 }}>
          {items.map((it) => {
            const sym = clean(it.symbol);
            const st = it.live_status || it.status;
            const triggered = st === "TRIGGERED";
            return (
              <article key={it.id} style={{ border: "1px solid rgba(139,92,246,0.22)", borderRadius: 10, padding: 13, background: "rgba(139,92,246,0.05)", display: "grid", gap: 9 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <div>
                    <Link href={`/stock/${encodeURIComponent(sym)}`} style={{ fontWeight: 850, color: "var(--text-primary)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
                      {sym} <ExternalLink size={11} />
                    </Link>
                    <div style={{ fontSize: "0.66rem", color: "var(--text-dim)", marginTop: 2 }}>
                      {it.pattern || "—"}{it.tag ? ` · ${it.tag}` : ""}{it.source === "RESEARCH_AUTO" ? " · from Research" : ""}
                    </div>
                  </div>
                  <StatusBadge status={st} />
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: "0.72rem" }}>
                  <Field k="Entry zone" v={`${inr(it.entry_low)}–${inr(it.entry_high)}`} />
                  <Field k="CMP" v={inr(it.cmp)} accent={st === "ACTIONABLE" ? "#34d399" : undefined} />
                  <Field k="SL" v={inr(it.stop_loss)} accent="#ff6b81" />
                  <Field k="Target" v={inr(it.target_2 ?? it.target_1)} accent="#00d18c" />
                  <Field k="Distance" v={it.distance_pct == null ? "—" : `${it.distance_pct > 0 ? "+" : ""}${it.distance_pct}%`} />
                  <Field k="Qty" v={it.calculated_quantity != null ? String(it.calculated_quantity) : "—"} />
                </div>

                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                  <button disabled={busy === it.id} onClick={() => act(it.id, () => api.watchlistMonitorBuyCmp(token, it.id))} style={btn("buy")}>
                    <ShoppingCart size={12} /> Buy CMP
                  </button>
                  {triggered ? (
                    <button disabled={busy === it.id} onClick={() => act(it.id, () => api.watchlistMonitorIgnore(token, it.id))} style={btn("ghost")}>Ignore</button>
                  ) : it.armed ? (
                    <span style={{ ...chip(), color: "#a78bfa", borderColor: "rgba(139,92,246,0.4)" }} title="We'll alert you when price reaches the entry zone">Alert Set ✓</span>
                  ) : (
                    <button disabled={busy === it.id} onClick={() => act(it.id, () => api.watchlistMonitorArm(token, it.id))} style={btn("arm")} title="Get alerted when price reaches the entry zone (no auto-buy)">
                      <Crosshair size={12} /> Set Entry Alert
                    </button>
                  )}
                  <Link href={`/research/chart?symbol=${encodeURIComponent(sym)}&horizon=SWING`} style={{ ...chip(), textDecoration: "none", color: "#34d399", borderColor: "rgba(16,185,129,0.4)" }}>
                    <Target size={12} /> Study
                  </Link>
                  <button disabled={busy === it.id} onClick={() => act(it.id, () => api.watchlistMonitorRemove(token, it.id))} style={{ ...btn("ghost"), marginLeft: "auto" }} title="Remove">
                    <Trash2 size={12} />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {showAdd && <AddModal token={token} onClose={() => setShowAdd(false)} onAdded={() => { setShowAdd(false); load(); }} />}
    </section>
  );
}

function Field({ k, v, accent }: { k: string; v: string; accent?: string }) {
  return (
    <div><span style={{ color: "var(--text-dim)" }}>{k}</span><br /><strong style={{ color: accent || "var(--text-primary)" }}>{v}</strong></div>
  );
}

function chip(): React.CSSProperties {
  return { fontSize: "0.7rem", fontWeight: 700, padding: "5px 9px", borderRadius: 7, display: "inline-flex", alignItems: "center", gap: 4, border: "1px solid var(--border)", background: "rgba(255,255,255,0.03)" };
}
function btn(kind: "primary" | "ghost" | "buy" | "arm"): React.CSSProperties {
  const base: React.CSSProperties = { fontSize: "0.72rem", fontWeight: 800, padding: "6px 10px", borderRadius: 7, cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 5, border: "1px solid var(--border)" };
  if (kind === "primary") return { ...base, color: "#04130d", background: "#34d399", border: "1px solid rgba(16,185,129,0.7)" };
  if (kind === "buy") return { ...base, color: "#04130d", background: "#34d399", border: "1px solid rgba(16,185,129,0.7)" };
  if (kind === "arm") return { ...base, color: "#a78bfa", background: "rgba(139,92,246,0.12)", border: "1px solid rgba(139,92,246,0.4)" };
  return { ...base, color: "var(--text-secondary)", background: "rgba(255,255,255,0.04)" };
}

function AddModal({ token, onClose, onAdded }: { token: string; onClose: () => void; onAdded: () => void }) {
  const [f, setF] = useState({ symbol: "", entry_low: "", entry_high: "", stop_loss: "", target_1: "", target_2: "", pattern: "", capital: "", risk_percent: "1" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  const submit = async () => {
    setError(null);
    const n = (s: string) => (s.trim() === "" ? null : Number(s));
    if (!f.symbol.trim() || !f.entry_low || !f.entry_high || !f.stop_loss) { setError("Symbol, entry zone and SL are required."); return; }
    setSaving(true);
    try {
      await api.watchlistMonitorAdd(token, {
        symbol: f.symbol, entry_low: Number(f.entry_low), entry_high: Number(f.entry_high), stop_loss: Number(f.stop_loss),
        target_1: n(f.target_1), target_2: n(f.target_2), pattern: f.pattern || null,
        capital: n(f.capital), risk_percent: n(f.risk_percent), source: "MANUAL",
      });
      onAdded();
    } catch (e) { setError(e instanceof Error ? e.message : "add failed"); } finally { setSaving(false); }
  };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "grid", placeItems: "center", zIndex: 60 }}>
      <div onClick={(e) => e.stopPropagation()} className="glass" style={{ width: "min(440px, 92vw)", padding: 18, borderRadius: 14, border: "1px solid rgba(139,92,246,0.4)", display: "grid", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 800 }}>Add to watchlist</h3>
          <button onClick={onClose} style={btn("ghost")}><X size={14} /></button>
        </div>
        <Inp label="Symbol (e.g. NETWEB)" v={f.symbol} on={(v) => set("symbol", v)} />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <Inp label="Entry low" v={f.entry_low} on={(v) => set("entry_low", v)} num />
          <Inp label="Entry high" v={f.entry_high} on={(v) => set("entry_high", v)} num />
          <Inp label="Stop loss" v={f.stop_loss} on={(v) => set("stop_loss", v)} num />
          <Inp label="Pattern (opt)" v={f.pattern} on={(v) => set("pattern", v)} />
          <Inp label="Target 1" v={f.target_1} on={(v) => set("target_1", v)} num />
          <Inp label="Target 2" v={f.target_2} on={(v) => set("target_2", v)} num />
          <Inp label="Capital (₹)" v={f.capital} on={(v) => set("capital", v)} num />
          <Inp label="Risk %" v={f.risk_percent} on={(v) => set("risk_percent", v)} num />
        </div>
        {error && <div style={{ fontSize: "0.74rem", color: "#fda4af" }}>{error}</div>}
        <button disabled={saving} onClick={submit} style={{ ...btn("primary"), justifyContent: "center", padding: "9px" }}>
          {saving ? "Adding…" : "Add to watchlist"}
        </button>
      </div>
    </div>
  );
}

function Inp({ label, v, on, num }: { label: string; v: string; on: (v: string) => void; num?: boolean }) {
  return (
    <label style={{ display: "grid", gap: 3, fontSize: "0.7rem", color: "var(--text-secondary)" }}>
      {label}
      <input value={v} inputMode={num ? "decimal" : "text"} onChange={(e) => on(e.target.value)}
        style={{ padding: "7px 9px", borderRadius: 7, border: "1px solid var(--border-interactive)", background: "rgba(0,0,0,0.25)", color: "var(--text-primary)", fontSize: "0.82rem" }} />
    </label>
  );
}
