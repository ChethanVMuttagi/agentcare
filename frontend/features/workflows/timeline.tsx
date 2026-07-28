import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { cn, formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { WorkflowTimelineEntry } from "@/types/api";

type EventCategory = "agent" | "tool" | "approval" | "failure" | "lifecycle";

const DOT_CLASSES: Record<EventCategory, string> = {
  agent: "border-violet-500 bg-violet-500",
  tool: "border-emerald-500 bg-emerald-500",
  approval: "border-amber-500 bg-amber-500",
  failure: "border-red-500 bg-red-500",
  lifecycle: "border-slate-400 bg-white dark:border-slate-500 dark:bg-slate-900",
};

function categorize(eventType: string): EventCategory {
  if (eventType === "agent_handoff") return "agent";
  if (eventType === "tool_invoked") return "tool";
  if (eventType.startsWith("approval_")) return "approval";
  if (eventType.endsWith("_failed")) return "failure";
  return "lifecycle";
}

/** A short, human-readable narrative line for the common AI-orchestration
 * event types — falls back to `null` (raw metadata JSON is shown
 * instead) for anything without a bespoke summary. */
function summarize(entry: WorkflowTimelineEntry): string | null {
  const metadata = entry.safe_metadata;
  if (!metadata) return null;

  if (entry.event_type === "agent_handoff") {
    const from = typeof metadata.from_agent === "string" ? metadata.from_agent : "?";
    const to = typeof metadata.to_agent === "string" ? metadata.to_agent : "?";
    return `${humanizeEnumValue(from)} handed off to ${humanizeEnumValue(to)}`;
  }
  if (entry.event_type === "tool_invoked" && typeof metadata.tool_name === "string") {
    return `Invoked ${metadata.tool_name}()`;
  }
  if (typeof metadata.decision_kind === "string") {
    return `Decision: ${humanizeEnumValue(metadata.decision_kind)}`;
  }
  return null;
}

export function Timeline({ entries }: { entries: WorkflowTimelineEntry[] }) {
  if (entries.length === 0) {
    return <EmptyState title="No events yet" description="Workflow activity will appear here as it happens." />;
  }

  return (
    <ol className="space-y-0">
      {entries.map((entry, index) => {
        const category = categorize(entry.event_type);
        const summary = summarize(entry);
        const metadataEntries = entry.safe_metadata ? Object.entries(entry.safe_metadata) : [];

        return (
          <li key={`${entry.sequence}-${entry.event_type}`} className="relative flex gap-4 pb-6 last:pb-0">
            {index < entries.length - 1 ? (
              <span className="absolute top-3 left-[7px] h-full w-px bg-slate-200 dark:bg-slate-800" aria-hidden />
            ) : null}
            <span
              className={cn("relative z-10 mt-1.5 h-3.5 w-3.5 shrink-0 rounded-full border-2", DOT_CLASSES[category])}
            />
            <div className="flex-1 pb-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {humanizeEnumValue(entry.event_type)}
                </p>
                {entry.step_agent_name ? <Badge tone="info">{entry.step_agent_name}</Badge> : null}
                <Badge tone="neutral">{humanizeEnumValue(entry.actor_type)}</Badge>
              </div>
              <p className="text-xs text-slate-400">
                {entry.actor_identifier} · {formatDateTime(entry.created_at)}
                {entry.step_type ? ` · ${humanizeEnumValue(entry.step_type)}` : ""}
              </p>
              {summary ? (
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{summary}</p>
              ) : metadataEntries.length > 0 ? (
                <pre className="mt-2 overflow-x-auto rounded-md bg-slate-50 p-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {JSON.stringify(entry.safe_metadata, null, 2)}
                </pre>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
