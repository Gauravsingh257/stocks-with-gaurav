import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Trading Journal",
  description: "Review logged signals, trades, research ideas, notes, and trade outcomes in one journal.",
  alternates: { canonical: "/journal" },
};

export default function JournalLayout({ children }: { children: React.ReactNode }) {
  return children;
}
