"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { initialLoginFormState } from "@/features/auth/action-state";
import { loginAction } from "@/features/auth/actions";

const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "staff", label: "Staff" },
  { value: "supervisor", label: "Supervisor" },
  { value: "patient", label: "Patient" },
];

export function LoginForm({
  defaultOrganizationId,
  defaultRole,
}: {
  defaultOrganizationId?: string;
  defaultRole?: string;
}) {
  const [state, formAction, pending] = useActionState(loginAction, initialLoginFormState);

  return (
    <form action={formAction} className="space-y-4">
      <div>
        <Label htmlFor="email">Email</Label>
        <Input id="email" name="email" type="email" autoComplete="username" required />
      </div>
      <div>
        <Label htmlFor="password">Password</Label>
        <Input id="password" name="password" type="password" autoComplete="current-password" required />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="organizationId">Organization ID</Label>
          <Input
            id="organizationId"
            name="organizationId"
            type="text"
            placeholder="UUID"
            defaultValue={defaultOrganizationId}
            required
          />
        </div>
        <div>
          <Label htmlFor="role">Role</Label>
          <Select id="role" name="role" defaultValue={defaultRole ?? "staff"} required>
            {ROLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        AgentCare has no directory lookup for organization membership yet, so we confirm access
        with a live check against the organization and role you enter here.
      </p>
      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" className="w-full" disabled={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
