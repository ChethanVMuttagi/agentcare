"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { initialAppointmentFormState } from "@/features/appointments/action-state";
import { cancelAppointmentAction } from "@/features/appointments/actions";

export function CancelForm({
  organizationId,
  appointmentId,
}: {
  organizationId: string;
  appointmentId: string;
}) {
  const [state, formAction, pending] = useActionState(
    cancelAppointmentAction.bind(null, organizationId, appointmentId),
    initialAppointmentFormState,
  );

  return (
    <form action={formAction} className="space-y-3">
      <div>
        <Label htmlFor="cancellation_reason">Cancellation reason (optional)</Label>
        <Textarea id="cancellation_reason" name="cancellation_reason" rows={2} />
      </div>
      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" variant="danger" disabled={pending}>
        {pending ? "Cancelling…" : "Cancel appointment"}
      </Button>
    </form>
  );
}
