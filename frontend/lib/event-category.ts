import type { WorkflowEventType } from "@/types/api";

export type EventCategory = "agent" | "tool" | "approval" | "failure" | "lifecycle";

/** Shared by the Timeline and Workflow Graph so both agree on what
 * counts as a "failure" (or tool call, or approval) event — see
 * `features/workflows/timeline.tsx` and `features/workflows/workflow-graph.tsx`. */
export function categorizeEvent(eventType: WorkflowEventType): EventCategory {
  if (eventType === "agent_handoff") return "agent";
  if (eventType === "tool_invoked") return "tool";
  if (eventType.startsWith("approval_")) return "approval";
  if (eventType.endsWith("_failed")) return "failure";
  return "lifecycle";
}

export type EventOutcome = "success" | "failure" | "waiting" | "resumed" | null;

/** The run/step lifecycle outcome a `*_completed`/`*_failed`/`*_waiting`/
 * `*_resumed` event represents, independent of `categorizeEvent`'s
 * "what kind of event is this" bucket — used to color success/error
 * badges on both the Timeline and the Workflow Graph's node status. */
export function eventOutcome(eventType: WorkflowEventType): EventOutcome {
  if (eventType.endsWith("_completed")) return "success";
  if (eventType.endsWith("_failed")) return "failure";
  if (eventType.endsWith("_waiting")) return "waiting";
  if (eventType.endsWith("_resumed")) return "resumed";
  return null;
}
