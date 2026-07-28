"use server";

import { redirect } from "next/navigation";

import type { CreatePatientFormState } from "@/features/patients/action-state";
import { ApiError, describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { createPatient } from "@/services/patients";

export async function createPatientAction(
  organizationId: string,
  _previousState: CreatePatientFormState,
  formData: FormData,
): Promise<CreatePatientFormState> {
  const session = await requireSession();

  const patientNumber = String(formData.get("patient_number") ?? "").trim();
  const firstName = String(formData.get("first_name") ?? "").trim();
  const lastName = String(formData.get("last_name") ?? "").trim();
  const dateOfBirth = String(formData.get("date_of_birth") ?? "").trim();

  if (!patientNumber || !firstName || !lastName || !dateOfBirth) {
    return { error: "All fields are required." };
  }

  let patientId: string;
  try {
    const patient = await createPatient({
      token: session.token,
      organizationId,
      data: {
        patient_number: patientNumber,
        first_name: firstName,
        last_name: lastName,
        date_of_birth: dateOfBirth,
      },
    });
    patientId = patient.id;
  } catch (error) {
    if (error instanceof ApiError && error.isConflict) {
      return { error: "A patient with that patient number already exists." };
    }
    return { error: describeError(error) };
  }

  redirect(`/org/${organizationId}/patients/${patientId}`);
}
