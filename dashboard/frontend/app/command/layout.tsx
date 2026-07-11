import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Command Center",
  description: "Your morning command center — what deserves your attention today: market mood, priority alerts, watchlist events, opportunities and the daily brief in one screen.",
  alternates: { canonical: "/command" },
};

export default function CommandLayout({ children }: { children: React.ReactNode }) {
  return children;
}
