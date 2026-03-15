"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  BarChart2,
  BookOpen,
  Bot,
  Eye,
  MessageSquare,
  Search,
  TrendingUp,
} from "lucide-react";

const PAGES = [
  { href: "/live", label: "Live Trading", icon: Activity },
  { href: "/charts", label: "SMC Charts", icon: TrendingUp },
  { href: "/analytics", label: "Analytics", icon: BarChart2 },
  { href: "/oi-intelligence", label: "OI Intelligence", icon: Eye },
  { href: "/research", label: "AI Research", icon: Bot },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/chat", label: "AI Chatbot", icon: MessageSquare },
];

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const router = useRouter();

  const filtered = query.trim()
    ? PAGES.filter(
        (p) =>
          p.label.toLowerCase().includes(query.toLowerCase()) ||
          p.href.toLowerCase().includes(query.toLowerCase())
      )
    : PAGES;

  const openPalette = useCallback(() => {
    setOpen(true);
    setQuery("");
    setSelected(0);
  }, []);

  const closePalette = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        if (!open) {
          setQuery("");
          setSelected(0);
        }
      }
      if (!open) return;
      if (e.key === "Escape") closePalette();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelected((s) => (s + 1) % Math.max(1, filtered.length));
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelected((s) => (s - 1 + filtered.length) % Math.max(1, filtered.length));
      }
      if (e.key === "Enter" && filtered[selected]) {
        e.preventDefault();
        closePalette();
        router.push(filtered[selected].href);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, closePalette, filtered, selected, router]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center pt-[15vh] px-4 bg-black/70 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onClick={closePalette}
    >
      <div
        className="terminal-card w-full max-w-lg overflow-hidden shadow-[0_0_20px_rgba(0,255,255,0.12)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 border-b border-cyan-500/20">
          <Search size={18} className="text-cyan-400 shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            placeholder="Search pages…"
            className="flex-1 bg-transparent text-slate-100 placeholder:text-gray-500 outline-none text-sm"
            autoFocus
          />
          <kbd className="text-[10px] text-gray-500 border border-gray-600 rounded px-1.5">ESC</kbd>
        </div>
        <div className="max-h-[60vh] overflow-y-auto py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-6 text-center text-gray-500 text-sm">No matches</div>
          ) : (
            filtered.map((page, i) => {
              const Icon = page.icon;
              return (
                <button
                  key={page.href}
                  type="button"
                  className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm transition-colors ${
                    i === selected
                      ? "bg-cyan-500/10 text-cyan-300 border-l-2 border-cyan-400"
                      : "text-slate-300 hover:bg-slate-800/50"
                  }`}
                  onMouseEnter={() => setSelected(i)}
                  onClick={() => {
                    closePalette();
                    router.push(page.href);
                  }}
                >
                  <Icon size={16} className="shrink-0 text-cyan-400/80" />
                  {page.label}
                </button>
              );
            })
          )}
        </div>
        <div className="px-4 py-2 border-t border-cyan-500/10 text-[10px] text-gray-500 flex justify-between">
          <span>↑↓ navigate</span>
          <span>Enter open</span>
        </div>
      </div>
    </div>
  );
}
