"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, BookOpen, BarChart2, Bot, TrendingUp, MessageSquare, Zap, Eye
} from "lucide-react";
import { SidebarBotWidget } from "@/components/FuturisticElements";

const NAV = [
  { href: "/live",            label: "Live Trading",    icon: Activity      },
  { href: "/analytics",       label: "Analytics",       icon: BarChart2     },
  { href: "/journal",         label: "Journal",         icon: BookOpen      },
  { href: "/research",        label: "AI Research Center", icon: Bot        },
  { href: "/oi-intelligence", label: "OI Intelligence", icon: Eye           },
  { href: "/charts",          label: "SMC Charts",      icon: TrendingUp    },
  { href: "/chat",            label: "AI Chatbot",      icon: MessageSquare },
];

export default function Sidebar() {
  const path = usePathname();

  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        background: "rgba(13,21,38,0.95)",
        borderRight: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        padding: "24px 12px",
        gap: 4,
        backdropFilter: "blur(12px)",
        position: "sticky",
        top: 0,
        height: "100vh",
        overflowY: "auto",
        zIndex: 10,
      }}
    >
      {/* Logo */}
      <div style={{ padding: "0 4px 24px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div
            style={{
              width: 32, height: 32, borderRadius: 8,
              background: "var(--accent-dim)",
              border: "1px solid var(--accent)",
              display: "flex", alignItems: "center", justifyContent: "center",
              boxShadow: "0 0 12px rgba(0,212,255,0.2)",
            }}
          >
            <Zap size={16} color="var(--accent)" />
          </div>
          <div>
            <div className="neon-text" style={{ fontSize: "0.82rem", fontWeight: 700, lineHeight: 1.2 }}>
              Stocks With Gaurav
            </div>
            <div style={{ fontSize: "0.62rem", color: "var(--text-secondary)", letterSpacing: "0.05em" }}>
              SMC DASHBOARD
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 8 }}>
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = path === href || (href !== "/" && path.startsWith(href));
          return (
            <Link key={href} href={href} className={`nav-link ${active ? "active" : ""}`}>
              <Icon size={16} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* AI Bot Widget */}
      <div style={{ marginTop: "auto", paddingTop: 16 }}>
        <SidebarBotWidget />
      </div>

      {/* Footer */}
      <div style={{ paddingTop: 12, borderTop: "1px solid var(--border)" }}>
        <div style={{ fontSize: "0.7rem", color: "var(--text-dim)", textAlign: "center" }}>
          AI Engine · Active
        </div>
      </div>
    </aside>
  );
}
