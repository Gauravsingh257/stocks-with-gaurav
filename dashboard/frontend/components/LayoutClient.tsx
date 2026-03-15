"use client";

import { useState } from "react";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import MobileNav from "@/components/MobileNav";
import MarketCommandBar from "@/components/MarketCommandBar";
import CommandPalette from "@/components/CommandPalette";

export default function LayoutClient({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="flex flex-1 min-w-0 overflow-x-hidden min-h-screen">
      <Sidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <div className="flex-1 flex flex-col min-w-0 relative z-[2] overflow-hidden">
        <MarketCommandBar />
        <TopBar onMenuClick={() => setSidebarOpen((v) => !v)} />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 pb-20 md:pb-6 md:p-6 lg:p-8">
          {children}
        </main>
      </div>
      <MobileNav />
      <CommandPalette />
    </div>
  );
}
