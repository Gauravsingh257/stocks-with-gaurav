"use client";
/**
 * Futuristic visual elements — AI Trading Bot, Cyber Grid, Animated Orbs.
 * Gives the dashboard a high-tech / sci-fi trading vibe.
 */

import { useEffect, useState } from "react";

/* ── Animated Background Grid + Scan Line ──────────────────────────────────── */
export function CyberGridOverlay() {
  return (
    <>
      <div className="cyber-grid-bg" />
      <div className="scan-line-effect" />
    </>
  );
}

/* ── Floating Orbs (ambient light blobs) ───────────────────────────────────── */
export function FloatingOrbs() {
  return (
    <div className="hidden md:block">
      <div className="orb" style={{ width: 300, height: 300, top: "10%", right: "-5%", background: "var(--accent)" }} />
      <div className="orb" style={{ width: 200, height: 200, bottom: "20%", left: "5%", background: "var(--success)", animationDelay: "-4s" }} />
      <div className="orb" style={{ width: 150, height: 150, top: "50%", right: "30%", background: "#7c3aed", animationDelay: "-8s" }} />
    </div>
  );
}

/* ── AI Trading Robot SVG ──────────────────────────────────────────────────── */
export function TradingBotSVG({ size = 180 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg">
      {/* Glow filter */}
      <defs>
        <filter id="neonGlow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <linearGradient id="botGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#00d4ff" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#7c3aed" stopOpacity="0.6" />
        </linearGradient>
        <linearGradient id="chartGrad" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#00e096" />
          <stop offset="100%" stopColor="#00d4ff" />
        </linearGradient>
      </defs>

      {/* Head / Helmet */}
      <rect x="55" y="20" width="90" height="70" rx="18" fill="rgba(13,21,38,0.9)" stroke="url(#botGrad)" strokeWidth="2" filter="url(#neonGlow)" />
      {/* Visor */}
      <rect x="65" y="38" width="70" height="22" rx="6" fill="rgba(0,212,255,0.1)" stroke="#00d4ff" strokeWidth="1.5" />
      {/* Eyes */}
      <circle cx="82" cy="49" r="5" fill="#00d4ff" opacity="0.9">
        <animate attributeName="opacity" values="0.9;0.4;0.9" dur="2.5s" repeatCount="indefinite" />
      </circle>
      <circle cx="118" cy="49" r="5" fill="#00d4ff" opacity="0.9">
        <animate attributeName="opacity" values="0.9;0.4;0.9" dur="2.5s" repeatCount="indefinite" begin="0.3s" />
      </circle>
      {/* Antenna */}
      <line x1="100" y1="20" x2="100" y2="8" stroke="#00d4ff" strokeWidth="1.5" />
      <circle cx="100" cy="6" r="3" fill="#00d4ff">
        <animate attributeName="r" values="3;4;3" dur="1.5s" repeatCount="indefinite" />
      </circle>

      {/* Body */}
      <rect x="60" y="95" width="80" height="55" rx="12" fill="rgba(13,21,38,0.9)" stroke="url(#botGrad)" strokeWidth="2" />
      {/* Chest indicator */}
      <circle cx="100" cy="110" r="8" fill="rgba(0,224,150,0.15)" stroke="#00e096" strokeWidth="1.5">
        <animate attributeName="r" values="8;10;8" dur="2s" repeatCount="indefinite" />
      </circle>
      <circle cx="100" cy="110" r="3" fill="#00e096" />

      {/* Mini chart on chest */}
      <polyline points="72,130 80,125 88,132 96,118 104,128 112,115 120,122 128,120" stroke="url(#chartGrad)" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round">
        <animate attributeName="points" values="72,130 80,125 88,132 96,118 104,128 112,115 120,122 128,120;72,128 80,132 88,125 96,130 104,118 112,128 120,115 128,125" dur="3s" repeatCount="indefinite" />
      </polyline>

      {/* Arms */}
      <rect x="38" y="100" width="18" height="40" rx="8" fill="rgba(13,21,38,0.9)" stroke="#00d4ff" strokeWidth="1.5" opacity="0.8" />
      <rect x="144" y="100" width="18" height="40" rx="8" fill="rgba(13,21,38,0.9)" stroke="#00d4ff" strokeWidth="1.5" opacity="0.8" />

      {/* Neck connector */}
      <rect x="90" y="88" width="20" height="10" rx="3" fill="rgba(0,212,255,0.2)" stroke="#00d4ff" strokeWidth="1" />

      {/* Legs */}
      <rect x="70" y="155" width="22" height="30" rx="6" fill="rgba(13,21,38,0.9)" stroke="url(#botGrad)" strokeWidth="1.5" />
      <rect x="108" y="155" width="22" height="30" rx="6" fill="rgba(13,21,38,0.9)" stroke="url(#botGrad)" strokeWidth="1.5" />

      {/* Feet glow */}
      <ellipse cx="81" cy="188" rx="14" ry="4" fill="rgba(0,212,255,0.15)">
        <animate attributeName="rx" values="14;16;14" dur="2s" repeatCount="indefinite" />
      </ellipse>
      <ellipse cx="119" cy="188" rx="14" ry="4" fill="rgba(0,212,255,0.15)">
        <animate attributeName="rx" values="14;16;14" dur="2s" repeatCount="indefinite" begin="0.5s" />
      </ellipse>

      {/* Data particles around */}
      <circle cx="30" cy="60" r="2" fill="#00d4ff" opacity="0.4">
        <animate attributeName="cy" values="60;30;60" dur="4s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.4;0.8;0.4" dur="4s" repeatCount="indefinite" />
      </circle>
      <circle cx="170" cy="80" r="1.5" fill="#00e096" opacity="0.3">
        <animate attributeName="cy" values="80;50;80" dur="3s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="0.3;0.7;0.3" dur="3s" repeatCount="indefinite" />
      </circle>
      <circle cx="45" cy="140" r="1.5" fill="#7c3aed" opacity="0.4">
        <animate attributeName="cy" values="140;110;140" dur="5s" repeatCount="indefinite" />
      </circle>
      <circle cx="160" cy="150" r="2" fill="#ffa502" opacity="0.3">
        <animate attributeName="cy" values="150;120;150" dur="3.5s" repeatCount="indefinite" />
      </circle>
    </svg>
  );
}

/* ── Sidebar Bot Widget ────────────────────────────────────────────────────── */
export function SidebarBotWidget() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick(p => p + 1), 3000);
    return () => clearInterval(t);
  }, []);

  const messages = [
    "Scanning markets...",
    "Analyzing SMC zones...",
    "Checking order blocks...",
    "Evaluating risk...",
    "Monitoring positions...",
    "AI engine active",
  ];

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 8,
      padding: "12px 8px",
      borderRadius: 12,
      background: "rgba(0,212,255,0.03)",
      border: "1px solid rgba(0,212,255,0.08)",
      position: "relative",
      overflow: "hidden",
    }}>
      <div className="sidebar-data-stream" />
      <TradingBotSVG size={80} />
      <div style={{
        fontSize: "0.62rem",
        color: "var(--accent)",
        letterSpacing: "0.06em",
        textAlign: "center",
        fontFamily: "monospace",
        opacity: 0.9,
      }}>
        {messages[tick % messages.length]}
      </div>
    </div>
  );
}

/* ── Hero Banner for Live Page ─────────────────────────────────────────────── */
export function HeroBanner() {
  return (
    <div style={{
      position: "relative",
      borderRadius: 16,
      overflow: "hidden",
      padding: "24px 32px",
      background: "linear-gradient(135deg, rgba(0,212,255,0.06) 0%, rgba(124,58,237,0.04) 50%, rgba(0,224,150,0.04) 100%)",
      border: "1px solid rgba(0,212,255,0.12)",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      gap: 24,
      minHeight: 120,
    }}>
      {/* Left: Text */}
      <div style={{ position: "relative", zIndex: 2 }}>
        <div style={{
          fontSize: "0.6rem",
          letterSpacing: "0.15em",
          color: "var(--accent)",
          fontWeight: 600,
          marginBottom: 6,
          fontFamily: "monospace",
        }}>
          AI-POWERED TRADING ENGINE
        </div>
        <h2 style={{
          fontSize: "1.1rem",
          fontWeight: 700,
          color: "var(--text-primary)",
          margin: "0 0 4px",
          lineHeight: 1.3,
        }}>
          Smart Money Concepts
        </h2>
        <p style={{
          fontSize: "0.75rem",
          color: "var(--text-secondary)",
          margin: 0,
          maxWidth: 340,
        }}>
          Real-time SMC detection with AI-driven order blocks, fair value gaps, and multi-timeframe analysis.
        </p>
      </div>

      {/* Right: Bot + animated elements */}
      <div style={{ position: "relative", flexShrink: 0 }}>
        <TradingBotSVG size={100} />
        {/* Ring effect */}
        <div style={{
          position: "absolute",
          inset: -10,
          borderRadius: "50%",
          border: "1px solid rgba(0,212,255,0.1)",
          animation: "pulse-dot 3s ease-in-out infinite",
        }} />
      </div>

      {/* Background decoration */}
      <div style={{
        position: "absolute",
        top: -20,
        right: -20,
        width: 200,
        height: 200,
        borderRadius: "50%",
        background: "radial-gradient(circle, rgba(0,212,255,0.08) 0%, transparent 70%)",
        pointerEvents: "none",
      }} />
      <div style={{
        position: "absolute",
        bottom: -30,
        left: "30%",
        width: 150,
        height: 150,
        borderRadius: "50%",
        background: "radial-gradient(circle, rgba(124,58,237,0.06) 0%, transparent 70%)",
        pointerEvents: "none",
      }} />
    </div>
  );
}

/* ── Live Ticker Strip (animated data) ─────────────────────────────────────── */
export function TickerStrip() {
  const items = [
    "NIFTY 50", "BANKNIFTY", "SMC ENGINE", "ORDER BLOCKS",
    "FVG ZONES", "LIQUIDITY SWEEPS", "AI ANALYSIS", "RISK MGMT"
  ];

  return (
    <div style={{
      overflow: "hidden",
      borderRadius: 8,
      background: "rgba(0,212,255,0.03)",
      border: "1px solid rgba(0,212,255,0.06)",
      padding: "6px 0",
      position: "relative",
    }}>
      <div style={{
        display: "flex",
        gap: 32,
        animation: "ticker-scroll 25s linear infinite",
        whiteSpace: "nowrap",
      }}>
        {[...items, ...items].map((item, i) => (
          <span key={i} style={{
            fontSize: "0.65rem",
            letterSpacing: "0.1em",
            color: "var(--text-secondary)",
            fontFamily: "monospace",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}>
            <span style={{
              width: 4, height: 4,
              borderRadius: "50%",
              background: i % 3 === 0 ? "var(--accent)" : i % 3 === 1 ? "var(--success)" : "#7c3aed",
              display: "inline-block",
            }} />
            {item}
          </span>
        ))}
      </div>
      <style>{`@keyframes ticker-scroll { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }`}</style>
    </div>
  );
}

/* ── Hexagon Pattern Decoration ────────────────────────────────────────────── */
export function HexPattern({ style }: { style?: React.CSSProperties }) {
  return (
    <svg width="120" height="120" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ opacity: 0.15, ...style }}>
      <defs>
        <pattern id="hexGrid" width="30" height="26" patternUnits="userSpaceOnUse">
          <polygon points="15,1 28,8 28,18 15,25 2,18 2,8" fill="none" stroke="#00d4ff" strokeWidth="0.5" />
        </pattern>
      </defs>
      <rect width="120" height="120" fill="url(#hexGrid)" />
    </svg>
  );
}
