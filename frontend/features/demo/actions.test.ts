import { beforeEach, describe, expect, it, vi } from "vitest";

import { runDemoScenarioAction } from "@/features/demo/actions";
import { requireSession } from "@/lib/require-session";
import { executeAgentRequest } from "@/services/agent";
import { getWorkflowTimeline } from "@/services/workflows";
import type { AgentExecuteResponse, WorkflowTimelineResponse } from "@/types/api";

vi.mock("@/lib/require-session", () => ({
  requireSession: vi.fn(),
}));
vi.mock("@/services/agent", () => ({
  executeAgentRequest: vi.fn(),
}));
vi.mock("@/services/workflows", () => ({
  getWorkflowTimeline: vi.fn(),
}));

const mockRequireSession = vi.mocked(requireSession);
const mockExecuteAgentRequest = vi.mocked(executeAgentRequest);
const mockGetWorkflowTimeline = vi.mocked(getWorkflowTimeline);

const ORG_ID = "org-1";

function agentResponse(): AgentExecuteResponse {
  return {
    workflow_id: "wf-1",
    workflow_status: "completed",
    decision_kind: "tool_call",
    handled_by_agent: "scheduling",
    message: "Booked.",
    tool_name: "book_appointment",
    tool_result_code: "appointment_booked",
    tool_result_data: null,
  };
}

function timeline(): WorkflowTimelineResponse {
  return { workflow_id: "wf-1", status: "completed", entries: [] };
}

describe("runDemoScenarioAction", () => {
  beforeEach(() => {
    mockRequireSession.mockReset();
    mockExecuteAgentRequest.mockReset();
    mockGetWorkflowTimeline.mockReset();
    mockRequireSession.mockResolvedValue({ token: "synthetic-test-token" });
  });

  it("runs the request through the real agent-execute service, then fetches its timeline", async () => {
    mockExecuteAgentRequest.mockResolvedValue(agentResponse());
    mockGetWorkflowTimeline.mockResolvedValue(timeline());

    const result = await runDemoScenarioAction(
      ORG_ID,
      "appointment_booking",
      "Book a follow-up.",
      { error: null, result: null },
    );

    expect(mockExecuteAgentRequest).toHaveBeenCalledWith({
      token: "synthetic-test-token",
      organizationId: ORG_ID,
      data: { request_type: "appointment_booking", request_text: "Book a follow-up." },
    });
    expect(mockGetWorkflowTimeline).toHaveBeenCalledWith({
      token: "synthetic-test-token",
      organizationId: ORG_ID,
      workflowId: "wf-1",
    });
    expect(result.error).toBeNull();
    expect(result.result?.status).toBe("completed");
  });

  it("surfaces a description of the error and no result when the agent call fails", async () => {
    mockExecuteAgentRequest.mockRejectedValue(new Error("Rate limit exceeded"));

    const result = await runDemoScenarioAction(ORG_ID, "administrative_routing", "Help.", {
      error: null,
      result: null,
    });

    expect(result.error).toBe("Rate limit exceeded");
    expect(result.result).toBeNull();
    expect(mockGetWorkflowTimeline).not.toHaveBeenCalled();
  });
});
