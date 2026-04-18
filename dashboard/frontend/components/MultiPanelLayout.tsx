"use client";

import { memo } from "react";
import Link from "next/link";
import { Eye, Bot } from "lucide-react";

const PANELS = [
  { href: "/oi-intelligence", label: "OI Intelligence", icon: Eye, desc: "PCR, heatmaps, bias" },
  { href: "/agents", label: "AI Signals", icon: Bot, desc: "Tactical plan, agents" },
];

function MultiPanelLayoutInner() {
  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4 md:p-6"
      role="region"
      aria-label="Trading terminal panels"
    >
      {PANELS.map(({ href, label, icon: Icon, desc }) => (
        <Link
          key={href}
          href={href}
          className="group flex flex-col rounded-xl border border-cyan-500/20 bg-slate-900/50 p-5 hover:border-cyan-500/40 hover:bg-slate-800/50 transition-colors"
        >
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-lg flex items-center justify-center bg-cyan-500/10 border border-cyan-500/20 group-hover:border-cyan-500/40">
              <Icon size={20} className="text-cyan-400" />
            </div>
            <span className="font-semibold text-slate-200 group-hover:text-cyan-300">{label}</span>
          </div>
          <p className="text-sm text-slate-400">{desc}</p>
        </Link>
      ))}
    </div>
  );
}

export default memo(MultiPanelLayoutInner);
