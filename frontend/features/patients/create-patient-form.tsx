"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { initialCreatePatientFormState } from "@/features/patients/action-state";
import { createPatientAction } from "@/features/patients/actions";

export function CreatePatientForm({ organizationId }: { organizationId: string }) {
  const [state, formAction, pending] = useActionState(
    createPatientAction.bind(null, organizationId),
    initialCreatePatientFormState,
  );

  return (
    <form action={formAction} className="max-w-lg space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="first_name">First name</Label>
          <Input id="first_name" name="first_name" required />
        </div>
        <div>
          <Label htmlFor="last_name">Last name</Label>
          <Input id="last_name" name="last_name" required />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <Label htmlFor="patient_number">Patient number</Label>
          <Input id="patient_number" name="patient_number" required />
        </div>
        <div>
          <Label htmlFor="date_of_birth">Date of birth</Label>
          <Input id="date_of_birth" name="date_of_birth" type="date" required />
        </div>
      </div>
      {state.error ? (
        <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Registering…" : "Register patient"}
      </Button>
    </form>
  );
}
