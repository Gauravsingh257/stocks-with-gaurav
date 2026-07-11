import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Product Health",
  description: "Internal product-health dashboard.",
  robots: { index: false, follow: false },
};

export default function HealthLayout({ children }: { children: React.ReactNode }) {
  return children;
}
