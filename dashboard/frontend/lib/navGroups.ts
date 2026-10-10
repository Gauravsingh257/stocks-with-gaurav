import {
  Home, Sparkles, Bot, Bookmark, LayoutDashboard, Globe, Activity,
  type LucideIcon,
} from "lucide-react";

/**
 * SINGLE SOURCE OF TRUTH for primary navigation.
 *
 * The desktop Sidebar and the mobile bottom bar both render this one tree, so
 * the two can never drift. Primary sections live in the sidebar; a section with
 * `children` expands in place to reveal its child pages — there is no second
 * horizontal tab strip duplicating these destinations anymore.
 *
 * Rule of thumb for what belongs here: *route navigation* (a link to another
 * page) belongs in this tree. *Views or modes within one page* (e.g. the
 * Research/Portfolio/Advanced modes on /research, or the 8 analytical views on
 * /intelligence) stay as in-page tabs on their own page and are NOT listed here.
 *
 * `match` lists the path prefixes that light a section up as active (so a child
 * route also highlights its parent). `href` on a parent is its own landing page
 * — clicking the label navigates there and opens the section.
 */

// Portfolio Intelligence Layer is default-ON; NEXT_PUBLIC_PIL_ENABLED=0 hides it
// and falls the Portfolio section back to Track Record + Journal only.
export const PIL_ENABLED = process.env.NEXT_PUBLIC_PIL_ENABLED !== "0";

export type NavChild = { href: string; label: string; match?: string[] };
export type NavSection = {
  href: string;
  label: string;
  icon: LucideIcon;
  auth?: boolean;   // only for signed-in users
  admin?: boolean;  // only for the operator
  match?: string[]; // prefixes that mark this section active
  children?: NavChild[];
};

const PORTFOLIO_SECTION: NavSection = PIL_ENABLED
  ? {
      href: "/intelligence", label: "Portfolio", icon: LayoutDashboard, auth: true,
      match: ["/intelligence", "/analytics", "/journal"],
      children: [
        { href: "/intelligence", label: "Overview" },
        { href: "/analytics", label: "Track Record" },
        { href: "/journal", label: "Journal" },
      ],
    }
  : {
      href: "/analytics", label: "Portfolio", icon: LayoutDashboard,
      match: ["/analytics", "/journal"],
      children: [
        { href: "/analytics", label: "Track Record" },
        { href: "/journal", label: "Journal" },
      ],
    };

export const NAV: NavSection[] = [
  { href: "/command", label: "Home", icon: Home },
  { href: "/terminal", label: "Terminal", icon: Sparkles },
  {
    href: "/research", label: "Research", icon: Bot,
    match: ["/research", "/screeners", "/sectors", "/universe"],
    children: [
      { href: "/research", label: "AI Research", match: ["/research/chart", "/research/compare", "/research/track-record"] },
      { href: "/screeners", label: "Screeners" },
      { href: "/sectors", label: "Sector Rotation" },
      { href: "/universe", label: "Stock Universe" },
    ],
  },
  { href: "/watchlist", label: "Watchlist", icon: Bookmark, auth: true },
  PORTFOLIO_SECTION,
  {
    href: "/oi-intelligence", label: "Markets", icon: Globe,
    match: ["/oi-intelligence", "/market-intelligence"],
    children: [
      { href: "/oi-intelligence", label: "Options (OI)" },
      { href: "/market-intelligence", label: "Market Intel" },
    ],
  },
  // Product Health is the operator's status page. /agents and /risk-engine are
  // deliberately NOT exposed here yet — they keep their URLs and admin access
  // until their user-facing role is decided.
  { href: "/health", label: "Product Health", icon: Activity, admin: true },
];

/**
 * Which top-level section a path belongs to (by href or any `match` prefix).
 * Used to auto-expand the active section in the sidebar.
 */
export function activeSectionHref(path: string | null | undefined): string | null {
  if (!path) return null;
  for (const s of NAV) {
    const prefixes = s.match ?? [s.href];
    if (prefixes.some((p) => path === p || (p !== "/" && path.startsWith(p)))) return s.href;
  }
  return null;
}
