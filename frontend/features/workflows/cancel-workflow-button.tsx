"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { initialWorkflowActionState } from "@/features/workflows/action-state";
import { cancelWorkflowAction } from "@/features/workflows/actions";

export function CancelWorkflowButton({
  organizationId,
  workflowId,
}: {
  organizationId: string;
  workflowId: string;
}) {
  const [state, formAction, pending] = useActionState(
    cancelWorkflowAction.bind(null, organizationId, workflowId),
    initialWorkflowActionState,
  );

  return (
    <form action={formAction} className="space-y-2">
      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" variant="danger" size="sm" disabled={pending}>
        {pending ? "Cancelling…" : "Cancel workflow"}
      </Button>
    </form>
  );
}
