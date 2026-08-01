import { RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { formatDuration, stepDurationsById, stepDurationsFromTimeline } from "@/lib/duration";
import { categorizeEvent, eventOutcome, type EventCategory, type EventOutcome } from "@/lib/event-category";
import { AgentIcon, EventCategoryIcon } from "@/lib/agent-icons";
import { cn, formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { WorkflowStepResponse, WorkflowTimelineEntry } from "@/types/api";

const DOT_CLASSES: Record<EventCategory, string> = {
  agent: "border-violet-500 bg-violet-500",
  tool: "border-emerald-500 bg-emerald-500",
  approval: "border-amber-500 bg-amber-500",
  failure: "border-red-500 bg-red-500",
  lifecycle: "border-slate-400 bg-white dark:border-slate-500 dark:bg-slate-900",
};

/** Lifecycle events (`categorizeEvent` -> `"lifecycle"`) get a more
 * specific dot color from their outcome — `*_completed` reads as green,
 * not the same neutral gray as `workflow_created`/`step_started`. */
const OUTCOME_DOT_OVERRIDE: Partial<Record<NonNullable<EventOutcome>, string>> = {
  success: "border-emerald-500 bg-emerald-500",
  waiting: "border-amber-500 bg-amber-500",
  resumed: "border-blue-500 bg-blue-500",
};

const OUTCOME_BADGE: Record<NonNullable<EventOutcome>, { tone: "success" | "danger" | "warning" | "info"; label: string }> = {
  success: { tone: "success", label: "Completed" },
  failure: { tone: "danger", label: "Failed" },
  waiting: { tone: "warning", label: "Waiting" },
  resumed: { tone: "info", label: "Resumed" },
};

function dotClassFor(category: EventCategory, outcome: EventOutcome): string {
  if (category === "lifecycle" && outcome && OUTCOME_DOT_OVERRIDE[outcome]) {
    return OUTCOME_DOT_OVERRIDE[outcome]!;
  }
  return DOT_CLASSES[category];
}

/** A short, human-readable narrative line for the common AI-orchestration
 * event types — falls back to `null` (raw metadata JSON is shown
 * instead) for anything without a bespoke summary. `step` (the matching
 * `WorkflowStepResponse`, when the caller fetched the step list
 * alongside the timeline) fills in details — like a tool's name on its
 * failure line, or the safe failure message — that the event's own
 * `safe_metadata` doesn't always carry (see
 * `app.services.workflow.WorkflowService.fail_step`, which records no
 * metadata of its own). */
function summarize(entry: WorkflowTimelineEntry, step: WorkflowStepResponse | undefined): string | null {
  const metadata = entry.safe_metadata;

  if (entry.event_type === "agent_handoff" && metadata) {
    const from = typeof metadata.from_agent === "string" ? metadata.from_agent : "?";
    const to = typeof metadata.to_agent === "string" ? metadata.to_agent : "?";
    return `${humanizeEnumValue(from)} handed off to ${humanizeEnumValue(to)}`;
  }
  if (entry.event_type === "tool_invoked") {
    const toolName = (metadata?.tool_name as string | undefined) ?? step?.tool_name ?? undefined;
    return toolName ? `Invoked ${toolName}()` : null;
  }
  if (entry.event_type === "step_completed") {
    const toolName = (metadata?.tool_name as string | undefined) ?? step?.tool_name ?? undefined;
    const resultCode = metadata?.result_code as string | undefined;
    if (toolName) {
      return `${toolName}() succeeded${resultCode ? ` — ${humanizeEnumValue(resultCode)}` : ""}`;
    }
  }
  if (entry.event_type === "step_failed" && step?.failure_message_safe) {
    return step.failure_message_safe;
  }
  if (metadata && typeof metadata.decision_kind === "string") {
    return `Decision: ${humanizeEnumValue(metadata.decision_kind)}`;
  }
  return null;
}

export function Timeline({
  entries,
  steps,
}: {
  entries: WorkflowTimelineEntry[];
  /** The sibling `WorkflowStepResponse[]` for this run, when the caller
   * already fetched it (see `services/workflows.ts#listWorkflowSteps`) —
   * gives exact per-step duration and retry/attempt counts. Timeline
   * still renders sensibly without it, deriving what it can from the
   * entries themselves. */
  steps?: WorkflowStepResponse[];
}) {
  if (entries.length === 0) {
    return <EmptyState title="No events yet" description="Workflow activity will appear here as it happens." />;
  }

  const stepsById = new Map((steps ?? []).map((step) => [step.id, step]));
  const durationByStepId = steps ? stepDurationsById(steps) : stepDurationsFromTimeline(entries);

  const resumeCountByStepId = new Map<string, number>();
  for (const entry of entries) {
    if (entry.event_type === "step_resumed" && entry.workflow_step_id) {
      resumeCountByStepId.set(
        entry.workflow_step_id,
        (resumeCountByStepId.get(entry.workflow_step_id) ?? 0) + 1,
      );
    }
  }

  return (
    <ol className="space-y-0">
      {entries.map((entry, index) => {
        const category = categorizeEvent(entry.event_type);
        const outcome = eventOutcome(entry.event_type);
        const step = entry.workflow_step_id ? stepsById.get(entry.workflow_step_id) : undefined;
        const summary = summarize(entry, step);
        const metadataEntries = entry.safe_metadata ? Object.entries(entry.safe_metadata) : [];
        const isTerminalStepEvent =
          entry.event_type === "step_completed" ||
          entry.event_type === "step_failed" ||
          entry.event_type === "step_skipped";
        const duration =
          isTerminalStepEvent && entry.workflow_step_id
            ? durationByStepId.get(entry.workflow_step_id)
            : undefined;
        const resumeCount = entry.workflow_step_id ? (resumeCountByStepId.get(entry.workflow_step_id) ?? 0) : 0;
        const attemptCount = step?.attempt_count ?? 1;
        const showRetryBadge = entry.event_type === "step_resumed" && (resumeCount > 0 || attemptCount > 1);

        return (
          <li key={`${entry.sequence}-${entry.event_type}`} className="relative flex gap-4 pb-6 last:pb-0">
            {index < entries.length - 1 ? (
              <span className="absolute top-3 left-[7px] h-full w-px bg-slate-200 dark:bg-slate-800" aria-hidden />
            ) : null}
            <span
              className={cn(
                "relative z-10 mt-1.5 h-3.5 w-3.5 shrink-0 rounded-full border-2",
                dotClassFor(category, outcome),
              )}
            />
            <div className="flex-1 pb-1">
              <div className="flex flex-wrap items-center gap-2">
                <EventCategoryIcon category={category} className="h-3.5 w-3.5 text-slate-400" />
                <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  {humanizeEnumValue(entry.event_type)}
                </p>
                {entry.step_agent_name ? (
                  <Badge tone="info" className="gap-1">
                    <AgentIcon name={entry.step_agent_name} className="h-3 w-3" />
                    {entry.step_agent_name}
                  </Badge>
                ) : null}
                <Badge tone="neutral">{humanizeEnumValue(entry.actor_type)}</Badge>
                {outcome ? (
                  <Badge tone={OUTCOME_BADGE[outcome].tone}>{OUTCOME_BADGE[outcome].label}</Badge>
                ) : null}
                {duration !== undefined && duration !== null ? (
                  <span className="text-xs font-medium text-slate-400">{formatDuration(duration)}</span>
                ) : null}
                {showRetryBadge ? (
                  <Badge tone="warning" className="gap-1">
                    <RotateCcw className="h-3 w-3" aria-hidden />
                    {attemptCount > 1 ? `Attempt ${attemptCount}` : `Resumed ${resumeCount}x`}
                  </Badge>
                ) : null}
              </div>
              <p className="text-xs text-slate-400">
                {entry.actor_identifier} · {formatDateTime(entry.created_at)}
                {entry.step_type ? ` · ${humanizeEnumValue(entry.step_type)}` : ""}
              </p>
              {category === "tool" ? (
                <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300">
                  <EventCategoryIcon category="tool" className="h-3.5 w-3.5 shrink-0" />
                  <span className="font-mono">{summary ?? "Tool invoked"}</span>
                </div>
              ) : summary ? (
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{summary}</p>
              ) : metadataEntries.length > 0 ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-slate-400 select-none hover:text-slate-600 dark:hover:text-slate-300">
                    Show payload
                  </summary>
                  <pre className="mt-1 overflow-x-auto rounded-md bg-slate-50 p-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {JSON.stringify(entry.safe_metadata, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
