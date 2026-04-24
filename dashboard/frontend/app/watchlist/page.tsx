"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Bookmark, Trash2, ExternalLink } from "lucide-react";
import { api, type SwingIdea, type LongTermIdea } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type IdeaUnion = (SwingIdea | LongTermIdea) & { _type: "SWING" | "LONGTERM" };

export default function WatchlistPage() {
  const { user, token } = useAuth();
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([]);
  const [ideas, setIdeas] = useState<Map<string, IdeaUnion>>(new Map());
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [wl, swingRes, ltRes] = await Promise.all([
        api.getWatchlist(token),
        api.swingResearch(50).catch(() => ({ items: [] })),
        api.longtermResearch(50).catch(() => ({ items: [] })),
      ]);
      const symbols = wl.items.map((i) => i.symbol);
      setWatchlistSymbols(symbols);

      const map = new Map<string, IdeaUnion>();
      (swingRes.items || []).forEach((item) => {
        const sym = item.symbol.replace("NSE:", "");
        if (symbols.includes(sym)) map.set(sym, { ...item, _type: "SWING" });
      });
      (ltRes.items || []).forEach((item) => {
        const sym = item.symbol.replace("NSE:", "");
        if (symbols.includes(sym) && !map.has(sym)) map.set(sym, { ...item, _type: "LONGTERM" });
      });
      setIdeas(map);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleRemove = async (symbol: string) => {
    if (!token) return;
    await api.removeFromWatchlist(token, symbol);
    setWatchlistSymbols((prev) => prev.filter((s) => s !== symbol));
  };

  if (!user) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Bookmark size={40} color="var(--text-dim)" style={{ margin: "0 auto 16px" }} />
        <h2 style={{ fontSize: "1.2rem", marginBottom: 8 }}>Sign in to use Watchlist</h2>
        <p style={{ color: "var(--text-secondary)", marginBottom: 20 }}>Save stocks you want to track and get updates.</p>
        <Link href="/login" className="btn-accent" style={{ textDecoration: "none", padding: "10px 24px" }}>Sign In</Link>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ width: 36, height: 36, borderRadius: 8, background: "rgba(0,212,255,0.1)", border: "1px solid rgba(0,212,255,0.2)", display: "grid", placeItems: "center" }}>
          <Bookmark size={17} color="var(--accent)" />
        </div>
        <div>
          <h1 style={{ margin: 0, fontSize: "1.3rem", fontWeight: 700 }}>Watchlist</h1>
          <p style={{ margin: 0, color: "var(--text-secondary)", fontSize: "0.8rem" }}>
            {watchlistSymbols.length} stocks tracked
          </p>
        </div>
      </div>

      {loading ? (
        <div className="glass" style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)" }}>Loading watchlist...</div>
      ) : watchlistSymbols.length === 0 ? (
        <div className="glass" style={{ padding: "32px 24px", textAlign: "center" }}>
          <Bookmark size={32} color="var(--text-dim)" style={{ margin: "0 auto 12px" }} />
          <p style={{ color: "var(--text-secondary)", marginBottom: 8 }}>Your watchlist is empty.</p>
          <p style={{ color: "var(--text-dim)", fontSize: "0.82rem" }}>
            Go to the <Link href="/research" style={{ color: "var(--accent)" }}>Research Center</Link> and bookmark stocks to track them here.
          </p>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
          {watchlistSymbols.map((symbol) => {
            const idea = ideas.get(symbol);
            return (
              <div key={symbol} className="glass" style={{ padding: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
                  <div>
                    <a
                      href={`https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(symbol)}`}
                      target="_blank" rel="noopener noreferrer"
                      style={{ color: "var(--accent)", fontWeight: 700, fontSize: "1rem", textDecoration: "none", display: "flex", alignItems: "center", gap: 4 }}
                    >
                      {symbol} <ExternalLink size={12} />
                    </a>
                    {idea && (
                      <span style={{
                        fontSize: "0.62rem", padding: "1px 6px", borderRadius: 3, marginTop: 4, display: "inline-block",
                        background: idea._type === "SWING" ? "rgba(91,156,246,0.12)" : "rgba(240,192,96,0.12)",
                        color: idea._type === "SWING" ? "#5b9cf6" : "#f0c060",
                      }}>
                        {idea._type} · {idea.setup}
                      </span>
                    )}
                  </div>
                  <button onClick={() => handleRemove(symbol)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-dim)", padding: 4 }} title="Remove from watchlist">
                    <Trash2 size={14} />
                  </button>
                </div>
                {idea ? (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: "0.78rem" }}>
                    <div><span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>Entry</span><br />₹{idea.entry_price.toFixed(2)}</div>
                    <div><span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>CMP</span><br />
                      <span style={{ color: idea.scan_cmp && idea.scan_cmp > idea.entry_price ? "#00e096" : "#ff4757" }}>
                        {idea.scan_cmp ? `₹${idea.scan_cmp.toFixed(2)}` : "—"}
                      </span>
                    </div>
                    <div><span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>R:R</span><br />{idea.risk_reward.toFixed(1)}x</div>
                    <div><span style={{ color: "var(--text-dim)", fontSize: "0.65rem" }}>Conf.</span><br />{idea.confidence_score.toFixed(1)}%</div>
                  </div>
                ) : (
                  <div style={{ fontSize: "0.78rem", color: "var(--text-dim)" }}>No active recommendation data</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
