import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names, resolving Tailwind conflicts sanely. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** `"Jordan Lee"` from first/last name fields — the one place this
 * formatting rule lives, so every table/card/detail view agrees. */
export function formatPersonName(first: string, last: string): string {
  return `${first} ${last}`.trim();
}

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

const dateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const timeFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
});

/** Format an ISO date/datetime string as `"Mar 4, 2026"`. Returns an
 * em dash for null/undefined so table cells never render "Invalid Date". */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return dateFormatter.format(date);
}

/** Format an ISO datetime string as `"Mar 4, 2026, 2:30 PM"`. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormatter.format(date);
}

/** Format an ISO datetime string as just the time, `"2:30 PM"`. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return timeFormatter.format(date);
}

/** `"snake_case_status"` -> `"Snake case status"` for any backend enum
 * value that doesn't have a dedicated label map (see components/ui/status-badge.tsx
 * for the ones that do). */
export function humanizeEnumValue(value: string): string {
  const spaced = value.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Runs a promise to completion and captures success/failure as data
 * instead of throwing. Used by Server Component pages that fetch, so
 * JSX construction can happen AFTER the `await` resolves rather than
 * inside a `try/catch` block — constructing JSX inside `try/catch` trips
 * the `react-hooks/error-boundaries` lint rule, since JSX isn't actually
 * rendered synchronously and a render error there wouldn't be caught by
 * the `catch` anyway.
 *
 * The `success` field is a literal-boolean discriminant (not a
 * `null`-vs-`unknown` check on `error`) so `if (!result.success)`
 * actually narrows `result.data` to `T` — `unknown` can't be narrowed by
 * a truthy/falsy check against `null` alone.
 */
export async function settle<T>(
  promise: Promise<T>,
): Promise<{ success: true; data: T } | { success: false; error: unknown }> {
  try {
    const data = await promise;
    return { success: true, data };
  } catch (error) {
    return { success: false, error };
  }
}

/** Bytes -> `"1.2 MB"`. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}
