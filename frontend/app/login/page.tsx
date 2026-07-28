import type { Metadata } from "next";

import { Card, CardContent } from "@/components/ui/card";
import { LoginForm } from "@/features/auth/login-form";
import { getLastOrganizationId, getRoleHint } from "@/lib/session";

export const metadata: Metadata = {
  title: "Sign in — AgentCare",
};

export default async function LoginPage() {
  const [organizationId, role] = await Promise.all([getLastOrganizationId(), getRoleHint()]);

  return (
    <div className="flex flex-1 items-center justify-center bg-slate-50 px-4 py-12 dark:bg-slate-950">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">AgentCare</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Sign in to continue</p>
        </div>
        <Card>
          <CardContent>
            <LoginForm defaultOrganizationId={organizationId ?? undefined} defaultRole={role ?? undefined} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
