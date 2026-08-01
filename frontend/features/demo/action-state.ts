import type { AgentExecuteResponse, WorkflowStatus, WorkflowTimelineEntry } from "@/types/api";

export interface DemoScenarioResult {
  response: AgentExecuteResponse;
  entries: WorkflowTimelineEntry[];
  status: WorkflowStatus;
}

export interface DemoScenarioState {
  error: string | null;
  /** The completed run's full trace — `scenario-card.tsx` plays this back
   * with a staged reveal instead of showing it all at once, so the demo
   * still feels like watching the agents work even though the run itself
   * (see `features/demo/actions.ts`) already finished by the time this
   * state lands. */
  result: DemoScenarioResult | null;
}

export const initialDemoScenarioState: DemoScenarioState = { error: null, result: null };
