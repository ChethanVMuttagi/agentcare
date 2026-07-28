"use server";

import { redirect } from "next/navigation";

import type { AppointmentFormState } from "@/features/appointments/action-state";
import { ApiError, describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { cancelAppointment, createAppointment, rescheduleAppointment } from "@/services/appointments";

export async function createAppointmentAction(
  organizationId: string,
  _previousState: AppointmentFormState,
  formData: FormData,
): Promise<AppointmentFormState> {
  const session = await requireSession();

  const practitionerId = String(formData.get("practitioner_id") ?? "");
  const departmentId = String(formData.get("department_id") ?? "");
  const startAt = String(formData.get("start_at") ?? "");
  const durationMinutes = Number(formData.get("duration_minutes") ?? 0);
  const patientId = String(formData.get("patient_id") ?? "").trim();

  if (!practitionerId || !departmentId || !startAt || !durationMinutes) {
    return { error: "Choose a department, practitioner, and time slot before booking." };
  }

  let appointmentId: string;
  try {
    const appointment = await createAppointment({
      token: session.token,
      organizationId,
      data: {
        practitioner_id: practitionerId,
        department_id: departmentId,
        start_at: startAt,
        duration_minutes: durationMinutes,
        patient_id: patientId || undefined,
      },
    });
    appointmentId = appointment.id;
  } catch (error) {
    if (error instanceof ApiError && error.isConflict) {
      return { error: "That time is no longer available. Pick another slot." };
    }
    return { error: describeError(error) };
  }

  redirect(`/org/${organizationId}/appointments/${appointmentId}`);
}

export async function rescheduleAppointmentAction(
  organizationId: string,
  appointmentId: string,
  _previousState: AppointmentFormState,
  formData: FormData,
): Promise<AppointmentFormState> {
  const session = await requireSession();

  const startAt = String(formData.get("start_at") ?? "");
  const durationMinutes = Number(formData.get("duration_minutes") ?? 0);

  if (!startAt || !durationMinutes) {
    return { error: "Choose a new date/time and duration." };
  }

  try {
    await rescheduleAppointment({
      token: session.token,
      organizationId,
      appointmentId,
      data: { start_at: new Date(startAt).toISOString(), duration_minutes: durationMinutes },
    });
  } catch (error) {
    if (error instanceof ApiError && error.isConflict) {
      return { error: "That time is no longer available. Pick another slot." };
    }
    return { error: describeError(error) };
  }

  redirect(`/org/${organizationId}/appointments/${appointmentId}`);
}

export async function cancelAppointmentAction(
  organizationId: string,
  appointmentId: string,
  _previousState: AppointmentFormState,
  formData: FormData,
): Promise<AppointmentFormState> {
  const session = await requireSession();
  const reason = String(formData.get("cancellation_reason") ?? "").trim();

  try {
    await cancelAppointment({
      token: session.token,
      organizationId,
      appointmentId,
      data: { cancellation_reason: reason || undefined },
    });
  } catch (error) {
    return { error: describeError(error) };
  }

  redirect(`/org/${organizationId}/appointments/${appointmentId}`);
}
