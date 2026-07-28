import Link from "next/link";

import { cn } from "@/lib/utils";

const PAGER_LINK_CLASSES =
  "inline-flex h-8 items-center justify-center rounded-md border border-slate-300 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800";
const PAGER_DISABLED_CLASSES =
  "inline-flex h-8 items-center justify-center rounded-md border border-slate-200 px-3 text-sm font-medium text-slate-300 dark:border-slate-800 dark:text-slate-600";

/**
 * Link-based pager (works without client JS) for `limit`/`offset` list
 * endpoints. None of the backend's list envelopes return a total count
 * (see the API catalog — `PatientListResponse`, `AppointmentListResponse`,
 * etc. are all just `{ <resource>: T[] }`), so this can't show "of N" or
 * know for certain there's a next page. Instead it uses the standard
 * limit/offset heuristic: a full page (`itemCount === limit`) implies
 * there MIGHT be more, so "Next" stays enabled; a short page means this
 * is the last one. `basePath` should already include any other query
 * params the page wants preserved; this only appends/overrides `offset`.
 */
export function Pagination({
  basePath,
  limit,
  offset,
  itemCount,
}: {
  basePath: string;
  limit: number;
  offset: number;
  itemCount: number;
}) {
  const hasPrevious = offset > 0;
  const hasNext = itemCount === limit;
  const previousOffset = Math.max(0, offset - limit);
  const nextOffset = offset + limit;
  const rangeStart = itemCount === 0 ? 0 : offset + 1;
  const rangeEnd = offset + itemCount;

  const withOffset = (value: number) => {
    const separator = basePath.includes("?") ? "&" : "?";
    return `${basePath}${separator}offset=${value}`;
  };

  return (
    <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
      <span>{itemCount === 0 ? "No results" : `Showing ${rangeStart}–${rangeEnd}`}</span>
      <div className="flex gap-2">
        {hasPrevious ? (
          <Link href={withOffset(previousOffset)} className={PAGER_LINK_CLASSES}>
            Previous
          </Link>
        ) : (
          <span className={cn(PAGER_DISABLED_CLASSES)}>Previous</span>
        )}
        {hasNext ? (
          <Link href={withOffset(nextOffset)} className={PAGER_LINK_CLASSES}>
            Next
          </Link>
        ) : (
          <span className={cn(PAGER_DISABLED_CLASSES)}>Next</span>
        )}
      </div>
    </div>
  );
}
