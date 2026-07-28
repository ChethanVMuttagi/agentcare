"use client";

import Link from "next/link";
import { useState } from "react";

import { useNotifications } from "@/hooks/use-notifications";
import { formatDateTime } from "@/lib/utils";
import type { Role } from "@/types/api";

export function NotificationCenter({
  organizationId,
  role,
}: {
  organizationId: string;
  role: Role | null;
}) {
  const canSeeApprovals = !role || role === "admin" || role === "staff" || role === "supervisor";
  const notifications = useNotifications(organizationId, { canSeeApprovals });
  const [open, setOpen] = useState(false);
  const [readCount, setReadCount] = useState(0);
  const unreadCount = Math.max(0, notifications.length - readCount);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          setOpen((previous) => !previous);
          setReadCount(notifications.length);
        }}
        aria-label="Notifications"
        className="relative rounded-md p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15 17h5l-1.4-1.4A2 2 0 0 1 18 14.2V11a6 6 0 0 0-4-5.7V5a2 2 0 1 0-4 0v.3A6 6 0 0 0 6 11v3.2a2 2 0 0 1-.6 1.4L4 17h5m6 0v1a3 3 0 1 1-6 0v-1m6 0H9"
          />
        </svg>
        {unreadCount > 0 ? (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        ) : null}
      </button>

      {open ? (
        <>
          <button
            type="button"
            aria-label="Close notifications"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-20"
          />
          <div className="absolute right-0 z-30 mt-2 w-80 rounded-lg border border-slate-200 bg-white shadow-lg dark:border-slate-800 dark:bg-slate-900">
            <div className="border-b border-slate-100 px-4 py-2 text-sm font-semibold text-slate-900 dark:border-slate-800 dark:text-slate-100">
              Notifications
            </div>
            <div className="max-h-80 overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="px-4 py-6 text-center text-sm text-slate-400">
                  Nothing yet — new approvals and finished workflows show up here.
                </p>
              ) : (
                notifications.map((notification) => (
                  <Link
                    key={notification.id}
                    href={notification.href}
                    onClick={() => setOpen(false)}
                    className="block border-b border-slate-50 px-4 py-3 text-sm last:border-0 hover:bg-slate-50 dark:border-slate-800/50 dark:hover:bg-slate-800/50"
                  >
                    <p className="text-slate-700 capitalize dark:text-slate-300">{notification.message}</p>
                    <p className="mt-0.5 text-xs text-slate-400">{formatDateTime(notification.createdAt)}</p>
                  </Link>
                ))
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
