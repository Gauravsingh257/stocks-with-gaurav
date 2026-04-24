import type { Metadata } from "next";
import "./globals.css";
import { CyberGridOverlay, FloatingOrbs } from "@/components/FuturisticElements";
import ErrorBoundary from "@/components/ErrorBoundary";
import LayoutClient from "@/components/LayoutClient";
import ThemeProvider from "@/components/ThemeProvider";
import { AuthProvider } from "@/lib/auth";

export const viewport = { width: "device-width", initialScale: 1 };

export const metadata: Metadata = {
  title: "Stocks With Gaurav - SMC Dashboard",
  description: "Stocks With Gaurav - SMC Trading Dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body
        className="flex min-h-screen overflow-x-hidden"
        style={{ background: "var(--bg-base)", position: "relative" }}
      >
        <CyberGridOverlay />
        <FloatingOrbs />
        <ErrorBoundary>
          <AuthProvider>
            <ThemeProvider>
              <LayoutClient>{children}</LayoutClient>
            </ThemeProvider>
          </AuthProvider>
        </ErrorBoundary>
      </body>
    </html>
  );
}
