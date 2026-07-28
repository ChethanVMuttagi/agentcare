"use client";

import { NotificationCenter } from "@/components/layout/notification-center";
import { logoutAction } from "@/features/auth/actions";
import { humanizeEnumValue } from "@/lib/utils";
import type { Role } from "@/types/api";

export function Topbar({
  organizationId,
  email,
  role,
  onMenuClick,
}: {
  organizationId: string;
  email: string;
  role: Role | null;
  onMenuClick: () => void;
}) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label="Toggle navigation"
          className="rounded-md p-2 text-slate-600 hover:bg-slate-100 md:hidden dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">AgentCare</span>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <NotificationCenter organizationId={organizationId} role={role} />
        <div className="hidden text-right sm:block">
          <p className="text-slate-700 dark:text-slate-300">{email}</p>
          {role ? <p className="text-xs text-slate-400">{humanizeEnumValue(role)}</p> : null}
        </div>
        <form action={logoutAction}>
          <button
            type="submit"
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Sign out
          </button>
        </form>
      </div>
    </header>
  );
}
