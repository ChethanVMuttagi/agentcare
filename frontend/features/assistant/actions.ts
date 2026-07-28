"use server";

import type { AssistantFormState } from "@/features/assistant/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { executeAgentRequest } from "@/services/agent";
import type { WorkflowRequestType } from "@/types/api";

export async function sendAssistantMessageAction(
  organizationId: string,
  _previousState: AssistantFormState,
  formData: FormData,
): Promise<AssistantFormState> {
  const session = await requireSession();

  const requestText = String(formData.get("request_text") ?? "").trim();
  const requestType = String(formData.get("request_type") ?? "") as WorkflowRequestType;
  const patientId = String(formData.get("patient_id") ?? "").trim();
  const workflowRunId = String(formData.get("workflow_run_id") ?? "").trim();

  if (!requestText) {
    return { response: null, error: "Enter a request for the assistant.", requestText: null };
  }

  try {
    const response = await executeAgentRequest({
      token: session.token,
      organizationId,
      data: {
        request_type: requestType,
        request_text: requestText,
        patient_id: patientId || undefined,
        workflow_run_id: workflowRunId || undefined,
      },
    });
    return { response, error: null, requestText };
  } catch (error) {
    return { response: null, error: describeError(error), requestText };
  }
}
