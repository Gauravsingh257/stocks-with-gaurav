import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar  from "@/components/TopBar";
import { CyberGridOverlay, FloatingOrbs } from "@/components/FuturisticElements";

export const metadata: Metadata = {
  title: "Stocks With Gaurav - SMC Dashboard",
  description: "Stocks With Gaurav - SMC Trading Dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ display: "flex", height: "100vh", background: "var(--bg-base)", position: "relative", overflow: "hidden" }}>
        {/* Ambient cyber effects */}
        <CyberGridOverlay />
        <FloatingOrbs />

        <Sidebar />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, position: "relative", zIndex: 2, overflow: "hidden" }}>
          <TopBar />
          <main style={{ flex: 1, padding: "24px", overflowY: "auto", overflowX: "hidden" }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
