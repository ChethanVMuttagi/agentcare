import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type { PatientCreate, PatientListResponse, PatientResponse } from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listPatients(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<PatientListResponse> {
  return apiFetch<PatientListResponse>(`/organizations/${params.organizationId}/patients`, {
    token: params.token,
    searchParams: { limit: params.limit ?? 50, offset: params.offset ?? 0 },
  });
}

export async function getPatient(
  params: OrgScoped & { patientId: string },
): Promise<PatientResponse> {
  return apiFetch<PatientResponse>(
    `/organizations/${params.organizationId}/patients/${params.patientId}`,
    { token: params.token },
  );
}

export async function getOwnPatient(params: OrgScoped): Promise<PatientResponse> {
  return apiFetch<PatientResponse>(`/organizations/${params.organizationId}/patients/me`, {
    token: params.token,
  });
}

export async function createPatient(
  params: OrgScoped & { data: PatientCreate },
): Promise<PatientResponse> {
  return apiFetch<PatientResponse>(`/organizations/${params.organizationId}/patients`, {
    token: params.token,
    method: "POST",
    body: params.data,
  });
}
