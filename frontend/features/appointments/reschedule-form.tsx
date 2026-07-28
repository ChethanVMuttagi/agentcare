"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { initialAppointmentFormState } from "@/features/appointments/action-state";
import { rescheduleAppointmentAction } from "@/features/appointments/actions";

const DURATION_OPTIONS = [15, 30, 45, 60];

export function RescheduleForm({
  organizationId,
  appointmentId,
  currentDurationMinutes,
}: {
  organizationId: string;
  appointmentId: string;
  currentDurationMinutes: number;
}) {
  const [state, formAction, pending] = useActionState(
    rescheduleAppointmentAction.bind(null, organizationId, appointmentId),
    initialAppointmentFormState,
  );

  return (
    <form action={formAction} className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="start_at">New date &amp; time</Label>
          <Input id="start_at" name="start_at" type="datetime-local" required />
        </div>
        <div>
          <Label htmlFor="duration_minutes">Duration</Label>
          <Select id="duration_minutes" name="duration_minutes" defaultValue={currentDurationMinutes}>
            {DURATION_OPTIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes} minutes
              </option>
            ))}
          </Select>
        </div>
      </div>
      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Rescheduling…" : "Reschedule"}
      </Button>
    </form>
  );
}
