import "server-only";

import { cookies } from "next/headers";

import { config } from "@/lib/config";
import type { Role } from "@/types/api";

const SESSION_MAX_AGE_SECONDS = 60 * 60 * 8; // 8 hours — matches typical backend JWT lifetime order of magnitude; the backend's own `exp` claim is still the real, enforced expiry.

/**
 * Session cookie strategy (see docs/FRONTEND.md "Authentication"):
 *
 * The backend JWT is stored ONLY in an httpOnly cookie — never in
 * localStorage, never sent to the browser as readable JS state. Every
 * backend call this app makes originates from the Next.js SERVER
 * (Server Components, Server Actions, or the `/api/backend` proxy route
 * — see `services/api-client.ts`), which reads this cookie and attaches
 * `Authorization: Bearer <token>` itself. The browser never needs, and
 * never gets, direct access to the token.
 *
 * `organization_id` and the role hint are NOT secrets — they are stored
 * in ordinary (non-httpOnly) cookies purely so the login form can
 * pre-fill the last-used values. Neither is ever trusted for
 * authorization: every backend route re-derives the caller's real role
 * from the database on every request (see the backend's
 * `app.auth.dependencies.get_current_membership`) — the role hint here
 * only decides which buttons/nav items this app SHOWS. Getting it wrong
 * changes nothing about what the backend will actually allow; see
 * `lib/session.ts`'s `getRoleHint` docstring.
 */

export interface Session {
  token: string;
}

export async function getSession(): Promise<Session | null> {
  const store = await cookies();
  const token = store.get(config.sessionCookieName)?.value;
  if (!token) return null;
  return { token };
}

export async function setSessionCookie(token: string): Promise<void> {
  const store = await cookies();
  store.set(config.sessionCookieName, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  store.delete(config.sessionCookieName);
}

export async function getLastOrganizationId(): Promise<string | null> {
  const store = await cookies();
  return store.get(config.lastOrgCookieName)?.value ?? null;
}

export async function setLastOrganizationId(organizationId: string): Promise<void> {
  const store = await cookies();
  store.set(config.lastOrgCookieName, organizationId, {
    httpOnly: false,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 90,
  });
}

/**
 * A UI-only hint of the caller's role, chosen at login (see
 * `features/auth/login-form.tsx`) because the backend exposes no
 * endpoint returning a user's memberships/roles (a known backend gap —
 * see the implementation report). This value is NEVER sent to the
 * backend as an authorization claim and NEVER used to decide whether an
 * API call is attempted — it only decides which nav items/buttons THIS
 * APP renders. Every actual write still goes to the real backend route,
 * which independently enforces the real role and returns 403 if the
 * hint was wrong; `components/ui/error-state.tsx` renders that
 * gracefully.
 */
export async function getRoleHint(): Promise<Role | null> {
  const store = await cookies();
  const value = store.get(config.roleCookieName)?.value;
  if (value === "admin" || value === "staff" || value === "patient" || value === "supervisor") {
    return value;
  }
  return null;
}

export async function setRoleHint(role: Role): Promise<void> {
  const store = await cookies();
  store.set(config.roleCookieName, role, {
    httpOnly: false,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 90,
  });
}

export async function clearRoleHint(): Promise<void> {
  const store = await cookies();
  store.delete(config.roleCookieName);
}
