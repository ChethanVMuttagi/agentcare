import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type {
  AppointmentCancelRequest,
  AppointmentCreate,
  AppointmentListResponse,
  AppointmentRescheduleRequest,
  AppointmentResponse,
} from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listAppointments(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<AppointmentListResponse> {
  return apiFetch<AppointmentListResponse>(
    `/organizations/${params.organizationId}/appointments`,
    { token: params.token, searchParams: { limit: params.limit ?? 50, offset: params.offset ?? 0 } },
  );
}

export async function getAppointment(
  params: OrgScoped & { appointmentId: string },
): Promise<AppointmentResponse> {
  return apiFetch<AppointmentResponse>(
    `/organizations/${params.organizationId}/appointments/${params.appointmentId}`,
    { token: params.token },
  );
}

export async function createAppointment(
  params: OrgScoped & { data: AppointmentCreate },
): Promise<AppointmentResponse> {
  return apiFetch<AppointmentResponse>(`/organizations/${params.organizationId}/appointments`, {
    token: params.token,
    method: "POST",
    body: params.data,
  });
}

export async function rescheduleAppointment(
  params: OrgScoped & { appointmentId: string; data: AppointmentRescheduleRequest },
): Promise<AppointmentResponse> {
  return apiFetch<AppointmentResponse>(
    `/organizations/${params.organizationId}/appointments/${params.appointmentId}/reschedule`,
    { token: params.token, method: "PATCH", body: params.data },
  );
}

export async function cancelAppointment(
  params: OrgScoped & { appointmentId: string; data: AppointmentCancelRequest },
): Promise<AppointmentResponse> {
  return apiFetch<AppointmentResponse>(
    `/organizations/${params.organizationId}/appointments/${params.appointmentId}/cancel`,
    { token: params.token, method: "POST", body: params.data },
  );
}
