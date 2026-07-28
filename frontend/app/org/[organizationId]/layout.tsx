import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { ApiError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { getRoleHint } from "@/lib/session";
import { getCurrentUser } from "@/services/auth";

export default async function OrgLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;
  const session = await requireSession();

  let email: string;
  try {
    const user = await getCurrentUser(session.token);
    email = user.email;
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthorized) {
      redirect("/login");
    }
    throw error;
  }

  const role = await getRoleHint();

  return (
    <AppShell organizationId={organizationId} role={role} email={email}>
      {children}
    </AppShell>
  );
}
