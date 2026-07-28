"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { initialApprovalActionState } from "@/features/approvals/action-state";
import { approveApprovalAction, rejectApprovalAction } from "@/features/approvals/actions";

export function ApprovalDecisionButtons({
  organizationId,
  approvalId,
}: {
  organizationId: string;
  approvalId: string;
}) {
  const [approveState, approveFormAction, approvePending] = useActionState(
    approveApprovalAction.bind(null, organizationId, approvalId),
    initialApprovalActionState,
  );
  const [rejectState, rejectFormAction, rejectPending] = useActionState(
    rejectApprovalAction.bind(null, organizationId, approvalId),
    initialApprovalActionState,
  );

  const error = approveState.error ?? rejectState.error;

  return (
    <div className="space-y-2">
      {error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      ) : null}
      <div className="flex gap-3">
        <form action={approveFormAction}>
          <Button type="submit" disabled={approvePending || rejectPending}>
            {approvePending ? "Approving…" : "Approve"}
          </Button>
        </form>
        <form action={rejectFormAction}>
          <Button type="submit" variant="danger" disabled={approvePending || rejectPending}>
            {rejectPending ? "Rejecting…" : "Reject"}
          </Button>
        </form>
      </div>
    </div>
  );
}
