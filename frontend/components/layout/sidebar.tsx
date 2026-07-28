"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_ITEMS } from "@/components/layout/nav-items";
import { cn } from "@/lib/utils";
import type { Role } from "@/types/api";

export function Sidebar({
  organizationId,
  role,
  className,
  onNavigate,
}: {
  organizationId: string;
  role: Role | null;
  className?: string;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const items = NAV_ITEMS.filter((item) => !role || item.roles.includes(role));

  return (
    <nav className={cn("flex flex-col gap-1", className)}>
      {items.map((item) => {
        const href = `/org/${organizationId}/${item.segment}`;
        const active = pathname?.startsWith(href) ?? false;
        return (
          <Link
            key={item.segment}
            href={href}
            onClick={onNavigate}
            className={cn(
              "rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
