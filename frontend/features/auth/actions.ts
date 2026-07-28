"use server";

import { redirect } from "next/navigation";

import type { LoginFormState } from "@/features/auth/action-state";
import { ApiError, describeError } from "@/lib/errors";
import { clearRoleHint, clearSessionCookie, setLastOrganizationId, setRoleHint, setSessionCookie } from "@/lib/session";
import { login, probeOrganizationAccess } from "@/services/auth";
import type { Role } from "@/types/api";

const ROLES: readonly Role[] = ["admin", "staff", "patient", "supervisor"];

function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

export async function loginAction(
  _previousState: LoginFormState,
  formData: FormData,
): Promise<LoginFormState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const organizationId = String(formData.get("organizationId") ?? "").trim();
  const roleValue = String(formData.get("role") ?? "");

  if (!email || !password) {
    return { error: "Email and password are required." };
  }
  if (!organizationId) {
    return { error: "Organization ID is required." };
  }
  if (!isRole(roleValue)) {
    return { error: "Select a role." };
  }

  let token: string;
  try {
    const tokenResponse = await login(email, password);
    token = tokenResponse.access_token;
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthorized) {
      return { error: "Invalid email or password." };
    }
    return { error: describeError(error) };
  }

  const hasAccess = await probeOrganizationAccess(token, organizationId, roleValue);
  if (!hasAccess) {
    return {
      error:
        "Could not verify access to that organization with the selected role. Double-check the Organization ID and role, then try again.",
    };
  }

  await setSessionCookie(token);
  await setLastOrganizationId(organizationId);
  await setRoleHint(roleValue);

  redirect(`/org/${organizationId}/dashboard`);
}

export async function logoutAction(): Promise<void> {
  await clearSessionCookie();
  await clearRoleHint();
  redirect("/login");
}
