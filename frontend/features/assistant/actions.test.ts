import { beforeEach, describe, expect, it, vi } from "vitest";

import { sendAssistantMessageAction } from "@/features/assistant/actions";
import { requireSession } from "@/lib/require-session";
import { executeAgentRequest } from "@/services/agent";
import type { AgentExecuteResponse } from "@/types/api";

vi.mock("@/lib/require-session", () => ({
  requireSession: vi.fn(),
}));
vi.mock("@/services/agent", () => ({
  executeAgentRequest: vi.fn(),
}));

const mockRequireSession = vi.mocked(requireSession);
const mockExecuteAgentRequest = vi.mocked(executeAgentRequest);

const ORG_ID = "org-1";

function agentResponse(): AgentExecuteResponse {
  return {
    workflow_id: "wf-1",
    workflow_status: "completed",
    decision_kind: "safe_response",
    handled_by_agent: "coordinator",
    message: "Here is your summary.",
    tool_name: null,
    tool_result_code: null,
    tool_result_data: null,
  };
}

function formData(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

describe("sendAssistantMessageAction", () => {
  beforeEach(() => {
    mockRequireSession.mockReset();
    mockExecuteAgentRequest.mockReset();
    mockRequireSession.mockResolvedValue({ token: "synthetic-test-token" });
  });

  it("rejects an empty request without calling the backend", async () => {
    const result = await sendAssistantMessageAction(
      ORG_ID,
      { response: null, error: null, requestText: null },
      formData({ request_text: "   ", request_type: "administrative_routing" }),
    );

    expect(result.error).toBe("Enter a request for the assistant.");
    expect(mockExecuteAgentRequest).not.toHaveBeenCalled();
  });

  it("sends a trimmed request and echoes it back alongside the response", async () => {
    mockExecuteAgentRequest.mockResolvedValue(agentResponse());

    const result = await sendAssistantMessageAction(
      ORG_ID,
      { response: null, error: null, requestText: null },
      formData({
        request_text: "  Book me an appointment  ",
        request_type: "appointment_booking",
        patient_id: "",
        workflow_run_id: "",
      }),
    );

    expect(mockExecuteAgentRequest).toHaveBeenCalledWith({
      token: "synthetic-test-token",
      organizationId: ORG_ID,
      data: {
        request_type: "appointment_booking",
        request_text: "Book me an appointment",
        patient_id: undefined,
        workflow_run_id: undefined,
      },
    });
    expect(result.requestText).toBe("Book me an appointment");
    expect(result.response?.message).toBe("Here is your summary.");
    expect(result.error).toBeNull();
  });

  it("forwards a workflow_run_id when continuing a clarification", async () => {
    mockExecuteAgentRequest.mockResolvedValue(agentResponse());

    await sendAssistantMessageAction(
      ORG_ID,
      { response: null, error: null, requestText: null },
      formData({
        request_text: "Tuesday afternoon",
        request_type: "appointment_booking",
        workflow_run_id: "wf-paused-1",
      }),
    );

    expect(mockExecuteAgentRequest).toHaveBeenCalledWith(
      expect.objectContaining({ data: expect.objectContaining({ workflow_run_id: "wf-paused-1" }) }),
    );
  });

  it("returns a description of the error, preserving the attempted request text", async () => {
    mockExecuteAgentRequest.mockRejectedValue(new Error("Too many requests. Please try again later."));

    const result = await sendAssistantMessageAction(
      ORG_ID,
      { response: null, error: null, requestText: null },
      formData({ request_text: "Book me an appointment", request_type: "appointment_booking" }),
    );

    expect(result.error).toBe("Too many requests. Please try again later.");
    expect(result.requestText).toBe("Book me an appointment");
    expect(result.response).toBeNull();
  });
});
