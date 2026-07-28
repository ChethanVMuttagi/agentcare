"use client";

import { useState } from "react";
import type { ReactNode } from "react";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { cn } from "@/lib/utils";
import type { Role } from "@/types/api";

export function AppShell({
  organizationId,
  role,
  email,
  children,
}: {
  organizationId: string;
  role: Role | null;
  email: string;
  children: ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex min-h-screen flex-col">
      <Topbar
        organizationId={organizationId}
        email={email}
        role={role}
        onMenuClick={() => setMobileOpen((open) => !open)}
      />
      <div className="flex flex-1">
        <aside
          className={cn(
            "w-64 shrink-0 border-r border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900",
            "absolute inset-y-14 z-20 md:static md:block",
            mobileOpen ? "block" : "hidden",
          )}
        >
          <Sidebar organizationId={organizationId} role={role} onNavigate={() => setMobileOpen(false)} />
        </aside>
        {mobileOpen ? (
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
            className="absolute inset-0 top-14 z-10 bg-black/30 md:hidden"
          />
        ) : null}
        <main className="min-w-0 flex-1 overflow-x-hidden p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}
