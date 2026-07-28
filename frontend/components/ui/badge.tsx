import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";
import { humanizeEnumValue } from "@/lib/utils";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  success: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  warning: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  danger: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  info: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ className, tone = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[tone],
        className,
      )}
      {...props}
    />
  );
}

/** Every status enum in the backend catalog (appointment/workflow/step/
 * approval/document) uses overlapping words with consistent meaning
 * (`pending`, `completed`, `failed`, `cancelled`, ...), so one tone map
 * covers all of them safely. */
const STATUS_TONES: Record<string, Tone> = {
  booked: "info",
  running: "info",
  available: "success",
  approved: "success",
  completed: "success",
  active: "success",
  pending: "warning",
  waiting: "warning",
  cancelled: "neutral",
  skipped: "neutral",
  deleted: "neutral",
  expired: "neutral",
  inactive: "neutral",
  failed: "danger",
  rejected: "danger",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const tone = STATUS_TONES[status.toLowerCase()] ?? "neutral";
  return (
    <Badge tone={tone} className={className}>
      {humanizeEnumValue(status)}
    </Badge>
  );
}
