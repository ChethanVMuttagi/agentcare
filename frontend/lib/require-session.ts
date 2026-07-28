import "server-only";

import { redirect } from "next/navigation";

import { getSession, type Session } from "@/lib/session";

/**
 * Defense-in-depth session check for Server Components (the PRIMARY
 * gate is `proxy.ts`, which redirects unauthenticated navigation before
 * a page ever renders — this exists so a gap in that matcher can never
 * silently expose data, the same "not the primary enforcement point"
 * layering the backend itself uses for its own defense-in-depth checks).
 */
export async function requireSession(): Promise<Session> {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }
  return session;
}
