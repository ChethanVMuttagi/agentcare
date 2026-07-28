/**
 * Server-only configuration. Never imported from a Client Component —
 * every value here either comes from a server-only env var or is
 * meaningless in the browser (the browser never talks to the FastAPI
 * backend directly; see `services/api-client.ts`).
 */

function requireEnv(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (!value) {
    throw new Error(
      `Missing required environment variable "${name}". Copy .env.local.example to .env.local and set it.`,
    );
  }
  return value;
}

export const config = {
  /** Base URL of the FastAPI backend, including the /api/v1 prefix. */
  apiBaseUrl: requireEnv("API_BASE_URL", "http://localhost:8000/api/v1"),
  /** Name of the httpOnly cookie holding the backend JWT. */
  sessionCookieName: process.env.SESSION_COOKIE_NAME || "agentcare_session",
  /** Name of the (non-sensitive) cookie remembering the last-used org id. */
  lastOrgCookieName: "agentcare_last_org",
  /** Name of the (non-sensitive) cookie remembering the selected UI role hint. */
  roleCookieName: "agentcare_role_hint",
} as const;
