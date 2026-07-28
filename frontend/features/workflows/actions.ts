"use server";

import { revalidatePath } from "next/cache";

import type { WorkflowActionState } from "@/features/workflows/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { cancelWorkflow } from "@/services/workflows";

export async function cancelWorkflowAction(
  organizationId: string,
  workflowId: string,
  _previousState: WorkflowActionState,
): Promise<WorkflowActionState> {
  const session = await requireSession();

  try {
    await cancelWorkflow({ token: session.token, organizationId, workflowId });
  } catch (error) {
    return { error: describeError(error) };
  }

  revalidatePath(`/org/${organizationId}/workflows/${workflowId}`);
  return { error: null };
}
