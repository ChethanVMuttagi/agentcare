"use server";

import type { DemoScenarioState } from "@/features/demo/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { executeAgentRequest } from "@/services/agent";
import { getWorkflowTimeline } from "@/services/workflows";
import type { WorkflowRequestType } from "@/types/api";

/**
 * Demo Mode (Milestone B/C): runs a canned prompt through the REAL AI
 * Assistant endpoint (`app.api.v1.endpoints.agent.execute_administrative_request`
 * — the exact same one `features/assistant/actions.ts` calls), then also
 * fetches the resulting workflow's full timeline so `scenario-card.tsx`
 * can play it back as a staged reveal on THIS page, without navigating
 * away first. Nothing here is scripted/mocked — every scenario is a
 * genuine orchestration run; only the reveal pacing is animated (see
 * `hooks/use-staged-reveal.ts`).
 */
export async function runDemoScenarioAction(
  organizationId: string,
  requestType: WorkflowRequestType,
  requestText: string,
  _previousState: DemoScenarioState,
): Promise<DemoScenarioState> {
  const session = await requireSession();

  try {
    const response = await executeAgentRequest({
      token: session.token,
      organizationId,
      data: { request_type: requestType, request_text: requestText },
    });
    const timeline = await getWorkflowTimeline({
      token: session.token,
      organizationId,
      workflowId: response.workflow_id,
    });
    return {
      error: null,
      result: { response, entries: timeline.entries, status: timeline.status },
    };
  } catch (error) {
    return { error: describeError(error), result: null };
  }
}
