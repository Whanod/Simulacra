// Routes that bypass the AuthGate even when Privy is configured. Add to
// this list when introducing routes that must remain anonymous (share
// links, embeds, marketing pages). Patterns are matched as either
// literal paths or regex strings prefixed with "re:".
export const PUBLIC_ROUTE_PATTERNS: ReadonlyArray<string> = [
  "re:^/r/[^/]+$",      // share short-link page
  "re:^/embed/",        // any /embed/... route
];

export function isPublicRoute(pathname: string): boolean {
  for (const pattern of PUBLIC_ROUTE_PATTERNS) {
    if (pattern.startsWith("re:")) {
      if (new RegExp(pattern.slice(3)).test(pathname)) return true;
    } else if (pattern === pathname) {
      return true;
    }
  }
  return false;
}
