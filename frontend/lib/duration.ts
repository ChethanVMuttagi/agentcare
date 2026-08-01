import type { WorkflowStepResponse, WorkflowTimelineEntry } from "@/types/api";

/** Elapsed milliseconds between two ISO timestamps, or `null` if either
 * is missing/unparseable (e.g. a step that hasn't finished yet). */
export function durationMs(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return null;
  return Math.max(0, endMs - startMs);
}

/** `340ms` / `1.2s` / `2m 5s` — `null`/`undefined` renders as an em dash,
 * matching `lib/utils.ts`'s date formatters. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

/** `workflow_step_id` -> elapsed duration, from each step's own
 * `started_at`/`completed_at` (the precise source, when the step list
 * has been fetched alongside the timeline). */
export function stepDurationsById(steps: WorkflowStepResponse[]): Map<string, number | null> {
  const map = new Map<string, number | null>();
  for (const step of steps) {
    map.set(step.id, durationMs(step.started_at, step.completed_at));
  }
  return map;
}

/** Fallback for callers with only timeline entries (no fetched step
 * list, e.g. the Workflow Graph's live SSE stream) — derives duration
 * per `workflow_step_id` from its first `step_started` event and its
 * last terminal (`step_completed`/`step_failed`/`step_skipped`) event. */
export function stepDurationsFromTimeline(
  entries: WorkflowTimelineEntry[],
): Map<string, number | null> {
  const started = new Map<string, string>();
  const ended = new Map<string, string>();
  for (const entry of entries) {
    if (!entry.workflow_step_id) continue;
    if (entry.event_type === "step_started" && !started.has(entry.workflow_step_id)) {
      started.set(entry.workflow_step_id, entry.created_at);
    }
    if (
      entry.event_type === "step_completed" ||
      entry.event_type === "step_failed" ||
      entry.event_type === "step_skipped"
    ) {
      ended.set(entry.workflow_step_id, entry.created_at);
    }
  }
  const map = new Map<string, number | null>();
  for (const [stepId, startedAt] of started) {
    map.set(stepId, durationMs(startedAt, ended.get(stepId) ?? null));
  }
  return map;
}
