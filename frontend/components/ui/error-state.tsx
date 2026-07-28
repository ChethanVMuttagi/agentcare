import { describeError } from "@/lib/errors";

/** Renders any thrown error (including `ApiError`) as a consistent inline
 * banner — the single place every page's error branch renders through,
 * so failures never surface as a raw stack trace or blank page. */
export function ErrorState({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-800 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-red-700 dark:text-red-400">{describeError(error)}</p>
    </div>
  );
}
