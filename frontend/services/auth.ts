import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type { CurrentUserResponse, Role, TokenResponse } from "@/types/api";

export async function login(email: string, password: string): Promise<TokenResponse> {
  return apiFetch<TokenResponse>("/auth/token", {
    method: "POST",
    body: { email, password },
  });
}

export async function getCurrentUser(token: string): Promise<CurrentUserResponse> {
  return apiFetch<CurrentUserResponse>("/auth/me", { token });
}

/**
 * The backend has no endpoint that returns a user's organization
 * memberships/roles, so this app asks for the organization id and a
 * role hint directly at login
 * and verifies BOTH by attempting one real, role-appropriate GET call —
 * never a fabricated/local check. A 200 means the org id is real and
 * this user genuinely has SOME active membership compatible with the
 * claimed role; 403/404 means the combination is wrong. This never
 * grants access on its own — it only decides whether the login form
 * shows an error before redirecting.
 */
export async function probeOrganizationAccess(
  token: string,
  organizationId: string,
  role: Role,
): Promise<boolean> {
  const probePath =
    role === "supervisor"
      ? `/organizations/${organizationId}/approvals`
      : `/organizations/${organizationId}/workflows`;
  try {
    await apiFetch(probePath, { token, searchParams: { limit: 1 } });
    return true;
  } catch {
    return false;
  }
}
