"use server";

import { redirect } from "next/navigation";

import type { DemoScenarioState } from "@/features/demo/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { executeAgentRequest } from "@/services/agent";
import type { WorkflowRequestType } from "@/types/api";

/**
 * Demo Mode (Milestone B): runs a canned prompt through the REAL AI
 * Assistant endpoint (`app.api.v1.endpoints.agent.execute_agent_request`
 * — the exact same one `features/assistant/actions.ts` calls) rather
 * than fabricating a fake workflow client-side, then redirects to that
 * workflow's AI Trace page so the Live Agent Execution panel picks it up
 * immediately. Nothing here is scripted/mocked — every scenario is a
 * genuine orchestration run, so the demo shows real handoffs, tool
 * calls, and decisions, not a canned recording.
 */
export async function runDemoScenarioAction(
  organizationId: string,
  requestType: WorkflowRequestType,
  requestText: string,
  _previousState: DemoScenarioState,
): Promise<DemoScenarioState> {
  const session = await requireSession();

  let workflowId: string;
  try {
    const response = await executeAgentRequest({
      token: session.token,
      organizationId,
      data: { request_type: requestType, request_text: requestText },
    });
    workflowId = response.workflow_id;
  } catch (error) {
    return { error: describeError(error) };
  }

  redirect(`/org/${organizationId}/workflows/${workflowId}`);
}
