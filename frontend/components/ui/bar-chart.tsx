import { cn, humanizeEnumValue } from "@/lib/utils";

const BAR_TONE_CLASSES = {
  neutral: "bg-slate-400 dark:bg-slate-600",
  info: "bg-blue-500",
  success: "bg-emerald-500",
} as const;

/** A minimal, dependency-free horizontal bar chart for a `{label: count}`
 * breakdown — used throughout the Analytics Dashboard. Bars are scaled
 * relative to the largest value in `data`, not a fixed axis. */
export function BreakdownBars({
  data,
  tone = "neutral",
}: {
  data: Record<string, number>;
  tone?: keyof typeof BAR_TONE_CLASSES;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);

  if (entries.length === 0) {
    return <p className="text-sm text-slate-400">No data yet.</p>;
  }

  const max = Math.max(1, ...entries.map(([, value]) => value));

  return (
    <div className="space-y-2">
      {entries.map(([key, value]) => (
        <div key={key} className="flex items-center gap-3 text-sm">
          <span className="w-36 shrink-0 truncate text-slate-600 dark:text-slate-300">
            {humanizeEnumValue(key)}
          </span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div
              className={cn("h-full rounded-full", BAR_TONE_CLASSES[tone])}
              style={{ width: `${(value / max) * 100}%` }}
            />
          </div>
          <span className="w-8 shrink-0 text-right font-medium text-slate-900 dark:text-slate-100">
            {value}
          </span>
        </div>
      ))}
    </div>
  );
}
