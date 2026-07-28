import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type { DepartmentListResponse, DepartmentResponse } from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listDepartments(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<DepartmentListResponse> {
  return apiFetch<DepartmentListResponse>(`/organizations/${params.organizationId}/departments`, {
    token: params.token,
    searchParams: { limit: params.limit ?? 100, offset: params.offset ?? 0 },
  });
}

export async function getDepartment(
  params: OrgScoped & { departmentId: string },
): Promise<DepartmentResponse> {
  return apiFetch<DepartmentResponse>(
    `/organizations/${params.organizationId}/departments/${params.departmentId}`,
    { token: params.token },
  );
}
