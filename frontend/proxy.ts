import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { config as appConfig } from "@/lib/config";

/**
 * Route protection for the org-scoped app shell. This is the PRIMARY
 * auth gate (see `lib/require-session.ts` for the defense-in-depth
 * Server Component check). Next.js 16 renamed `middleware.ts` to
 * `proxy.ts` — see node_modules/next/dist/docs/.../file-conventions/proxy.md.
 *
 * This only checks that a session cookie is PRESENT, not that the JWT is
 * still valid — token validity is enforced by the backend on every
 * request. A stale/expired token still reaches a page, which then gets a
 * 401 from the backend and renders through `<ErrorState>`.
 */
export function proxy(request: NextRequest) {
  const hasSession = request.cookies.has(appConfig.sessionCookieName);
  if (!hasSession) {
    const loginUrl = new URL("/login", request.url);
    return NextResponse.redirect(loginUrl);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/org/:path*"],
};
