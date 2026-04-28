import type { Metadata } from "next";
import "./globals.css";
import { CyberGridOverlay, FloatingOrbs } from "@/components/FuturisticElements";
import ErrorBoundary from "@/components/ErrorBoundary";
import LayoutClient from "@/components/LayoutClient";
import ThemeProvider from "@/components/ThemeProvider";
import { AuthProvider } from "@/lib/auth";
import { site } from "@/lib/site";
import { Analytics } from "@vercel/analytics/react";

export const viewport = { width: "device-width", initialScale: 1 };

export const metadata: Metadata = {
  metadataBase: new URL(site.url),
  title: {
    default: site.title,
    template: `%s | ${site.name}`,
  },
  description: site.description,
  applicationName: site.name,
  creator: site.name,
  publisher: site.name,
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: site.title,
    description: site.description,
    url: site.url,
    siteName: site.name,
    type: "website",
    images: [{ url: site.ogImage, width: 1200, height: 630, alt: site.title }],
  },
  twitter: {
    card: "summary_large_image",
    title: site.title,
    description: site.description,
    images: [site.ogImage],
  },
  robots: {
    index: true,
    follow: true,
  },
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
        <Analytics />
      </body>
    </html>
  );
}
