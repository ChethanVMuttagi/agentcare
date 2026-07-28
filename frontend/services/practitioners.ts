import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type {
  AvailableTimesResponse,
  PractitionerListResponse,
  PractitionerResponse,
} from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listPractitioners(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<PractitionerListResponse> {
  return apiFetch<PractitionerListResponse>(
    `/organizations/${params.organizationId}/practitioners`,
    { token: params.token, searchParams: { limit: params.limit ?? 100, offset: params.offset ?? 0 } },
  );
}

export async function getPractitioner(
  params: OrgScoped & { practitionerId: string },
): Promise<PractitionerResponse> {
  return apiFetch<PractitionerResponse>(
    `/organizations/${params.organizationId}/practitioners/${params.practitionerId}`,
    { token: params.token },
  );
}

export async function listAvailableTimes(
  params: OrgScoped & {
    practitionerId: string;
    departmentId: string;
    date: string;
    durationMinutes: number;
  },
): Promise<AvailableTimesResponse> {
  return apiFetch<AvailableTimesResponse>(
    `/organizations/${params.organizationId}/practitioners/${params.practitionerId}/available-times`,
    {
      token: params.token,
      searchParams: {
        department_id: params.departmentId,
        date: params.date,
        duration_minutes: params.durationMinutes,
      },
    },
  );
}
