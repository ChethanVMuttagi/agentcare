import { durationMs } from "@/lib/duration";
import type { WorkflowRunResponse, WorkflowTimelineResponse } from "@/types/api";

/** `workflows_by_status.completed / (completed + failed + cancelled)` —
 * a pure derivation from the existing `AnalyticsSummaryResponse`
 * (`services/analytics.ts#getAnalyticsSummary`), no extra fetch needed.
 * `null` when nothing has reached a decided outcome yet. */
export function computeSuccessRate(workflowsByStatus: Record<string, number>): number | null {
  const completed = workflowsByStatus.completed ?? 0;
  const failed = workflowsByStatus.failed ?? 0;
  const cancelled = workflowsByStatus.cancelled ?? 0;
  const decided = completed + failed + cancelled;
  if (decided === 0) return null;
  return completed / decided;
}

/** Average `completed_at - started_at` across a sample of runs (see
 * `services/workflows.ts#listWorkflows` — the same run list the daily
 * trend below is computed from). `null` when none of the sample has
 * both timestamps yet. */
export function computeAverageDurationMs(workflows: WorkflowRunResponse[]): number | null {
  const durations = workflows
    .map((workflow) => durationMs(workflow.started_at, workflow.completed_at))
    .filter((value): value is number => value !== null);
  if (durations.length === 0) return null;
  return durations.reduce((sum, value) => sum + value, 0) / durations.length;
}

export interface DailyTrendPoint {
  /** `YYYY-MM-DD`. */
  date: string;
  count: number;
}

/** Workflow count by calendar day (UTC) over the trailing `days` days,
 * from the same run sample `computeAverageDurationMs` uses — zero-filled
 * so a quiet day still shows as a point, not a gap. */
export function computeDailyTrend(workflows: WorkflowRunResponse[], days = 14): DailyTrendPoint[] {
  const countByDate = new Map<string, number>();
  for (const workflow of workflows) {
    const date = workflow.created_at.slice(0, 10);
    countByDate.set(date, (countByDate.get(date) ?? 0) + 1);
  }

  const points: DailyTrendPoint[] = [];
  const today = new Date();
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const day = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - offset));
    const key = day.toISOString().slice(0, 10);
    points.push({ date: key, count: countByDate.get(key) ?? 0 });
  }
  return points;
}

/** Tool-name -> invocation count, from a SAMPLE of recent workflows'
 * timelines (`services/workflows.ts#getWorkflowTimeline`) — there is no
 * backend aggregate broken down by tool name (only a total, in
 * `AnalyticsSummaryResponse.tool_invocations_total`), so this is
 * deliberately fetched for a bounded, recent sample rather than every
 * workflow ever run — see `app/org/[organizationId]/analytics/page.tsx`,
 * which labels this section as sampled, recent activity. */
export function computeToolUsage(timelines: WorkflowTimelineResponse[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const timeline of timelines) {
    for (const entry of timeline.entries) {
      if (entry.event_type !== "tool_invoked") continue;
      const toolName = entry.safe_metadata?.tool_name;
      if (typeof toolName === "string") {
        counts[toolName] = (counts[toolName] ?? 0) + 1;
      }
    }
  }
  return counts;
}
