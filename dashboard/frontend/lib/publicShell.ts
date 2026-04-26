/** Routes that skip dashboard chrome (sidebar, market bar, engine strip). */
export function isPublicDashboardPath(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  if (pathname === "/") return true;
  if (pathname === "/login" || pathname.startsWith("/login/")) return true;
  if (pathname === "/register" || pathname.startsWith("/register/")) return true;
  return false;
}
