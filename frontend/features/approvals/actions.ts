"use server";

import { revalidatePath } from "next/cache";

import type { ApprovalActionState } from "@/features/approvals/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { approveApproval, rejectApproval } from "@/services/approvals";

export async function approveApprovalAction(
  organizationId: string,
  approvalId: string,
  _previousState: ApprovalActionState,
): Promise<ApprovalActionState> {
  const session = await requireSession();
  try {
    await approveApproval({ token: session.token, organizationId, approvalId });
  } catch (error) {
    return { error: describeError(error) };
  }
  revalidatePath(`/org/${organizationId}/approvals/${approvalId}`);
  return { error: null };
}

export async function rejectApprovalAction(
  organizationId: string,
  approvalId: string,
  _previousState: ApprovalActionState,
): Promise<ApprovalActionState> {
  const session = await requireSession();
  try {
    await rejectApproval({ token: session.token, organizationId, approvalId });
  } catch (error) {
    return { error: describeError(error) };
  }
  revalidatePath(`/org/${organizationId}/approvals/${approvalId}`);
  return { error: null };
}
