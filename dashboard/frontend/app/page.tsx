import type { Metadata } from "next";
import Link from "next/link";
import {
  BarChart3,
  BookOpen,
  CheckCircle2,
  Search,
  ShieldCheck,
  Target,
} from "lucide-react";
import { site } from "@/lib/site";

export const metadata: Metadata = {
  title: "Educational SMC Research for NSE Traders",
  description:
    "Explore an educational SMC research dashboard for NSE watchlists, market structure, journal analytics, and transparent algorithmic research.",
  alternates: { canonical: "/" },
};

const stats = [
  { label: "Research stages", value: "3", note: "Discovery, watchlist, final review" },
  { label: "Market focus", value: "NSE", note: "Indian equities and index context" },
  { label: "Risk framework", value: "R", note: "Entry, stop loss, target, R:R" },
];

const features = [
  {
    icon: Search,
    title: "Structured SMC Discovery",
    body: "Screens stocks through market structure, liquidity context, and quality checks before anything reaches the watchlist.",
  },
  {
    icon: Target,
    title: "Defined Risk Levels",
    body: "Shows planned entry, stop loss, targets, and risk-reward so every idea can be studied before any manual action.",
  },
  {
    icon: BarChart3,
    title: "Track Record Visibility",
    body: "Keeps research outcomes visible with active, resolved, target-hit, stop-hit, and performance summaries.",
  },
  {
    icon: BookOpen,
    title: "Journal And Analytics",
    body: "Connects signals, trades, research history, and analytics so decisions can be reviewed instead of guessed.",
  },
];

const process = [
  "Discovery: early SMC evidence is logged and filtered.",
  "Watchlist: near-entry setups are monitored for confirmation.",
  "Final review: high-conviction ideas are presented for manual study.",
];

export default function Home() {
  return (
    <main className="public-home">
      <nav className="public-nav" aria-label="Public navigation">
        <Link href="/" className="public-brand" aria-label={site.name}>
          <span className="public-brand-mark">SG</span>
          <span>{site.name}</span>
        </Link>
        <div className="public-nav-actions">
          <Link href="/research/track-record">Track Record</Link>
          <Link href="/login">Sign In</Link>
          <Link href="/research" className="public-nav-primary">Open Dashboard</Link>
        </div>
      </nav>

      <section className="public-hero">
        <div className="public-hero-copy">
          <div className="public-eyebrow">
            <ShieldCheck size={16} />
            Educational SMC research for Indian markets
          </div>
          <h1>Study NSE setups with a transparent Smart Money Concepts dashboard.</h1>
          <p>
            Stocks With Gaurav turns market structure, liquidity zones, risk levels,
            journal history, and research outcomes into one calm workflow for learning
            and reviewing trade ideas.
          </p>
          <div className="public-cta-row">
            <Link href="/research" className="public-primary-cta">Explore Research</Link>
            <Link href="/research/track-record" className="public-secondary-cta">View Track Record</Link>
          </div>
          <p className="public-risk-note">
            Educational and informational only. Not SEBI-registered. No buy, sell,
            or hold recommendations are provided.
          </p>
        </div>

        <div className="public-product-visual" aria-label="Dashboard preview">
          <div className="preview-topline">
            <span>SMC RESEARCH PIPELINE</span>
            <span className="preview-live-dot">Engine connected</span>
          </div>
          <div className="preview-chart">
            <div className="preview-zone zone-a" />
            <div className="preview-zone zone-b" />
            <svg viewBox="0 0 640 260" role="img" aria-label="Market structure preview">
              <path d="M26 207 C84 184 107 222 160 181 S235 97 288 126 S384 194 431 141 S516 52 612 75" />
              <circle cx="160" cy="181" r="6" />
              <circle cx="431" cy="141" r="6" />
            </svg>
          </div>
          <div className="preview-grid">
            <PreviewMetric label="Discovery" value="Quality checked" />
            <PreviewMetric label="Watchlist" value="Near entry" />
            <PreviewMetric label="Final review" value="Risk mapped" />
          </div>
        </div>
      </section>

      <section className="public-stats" aria-label="Platform summary">
        {stats.map((item) => (
          <div key={item.label} className="public-stat">
            <strong>{item.value}</strong>
            <span>{item.label}</span>
            <small>{item.note}</small>
          </div>
        ))}
      </section>

      <section className="public-section">
        <div className="public-section-heading">
          <span>What the dashboard does</span>
          <h2>Built for repeatable research, not random tips.</h2>
        </div>
        <div className="public-feature-grid">
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <article key={feature.title} className="public-feature">
                <Icon size={20} />
                <h3>{feature.title}</h3>
                <p>{feature.body}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="public-section public-process">
        <div className="public-section-heading">
          <span>Research flow</span>
          <h2>Every idea moves through visible stages.</h2>
        </div>
        <div className="public-process-list">
          {process.map((item) => (
            <div key={item} className="public-process-item">
              <CheckCircle2 size={18} />
              <span>{item}</span>
            </div>
          ))}
        </div>
        <Link href="/research" className="public-primary-cta public-section-cta">
          Open Research Dashboard
        </Link>
      </section>

      <footer className="public-footer">
        <div>
          <strong>{site.name}</strong>
          <p>Educational SMC research dashboard for NSE market study.</p>
        </div>
        <div className="public-footer-links">
          <Link href="/analytics">Analytics</Link>
          <Link href="/journal">Journal</Link>
          <Link href="/market-intelligence">Market Intel</Link>
        </div>
      </footer>
    </main>
  );
}

function PreviewMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
