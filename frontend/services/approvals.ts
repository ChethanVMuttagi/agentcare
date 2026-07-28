import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type {
  ApprovalRequestCreate,
  ApprovalRequestListResponse,
  ApprovalRequestResponse,
} from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listApprovals(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<ApprovalRequestListResponse> {
  return apiFetch<ApprovalRequestListResponse>(
    `/organizations/${params.organizationId}/approvals`,
    { token: params.token, searchParams: { limit: params.limit ?? 50, offset: params.offset ?? 0 } },
  );
}

export async function getApproval(
  params: OrgScoped & { approvalId: string },
): Promise<ApprovalRequestResponse> {
  return apiFetch<ApprovalRequestResponse>(
    `/organizations/${params.organizationId}/approvals/${params.approvalId}`,
    { token: params.token },
  );
}

export async function createApproval(
  params: OrgScoped & { data: ApprovalRequestCreate },
): Promise<ApprovalRequestResponse> {
  return apiFetch<ApprovalRequestResponse>(`/organizations/${params.organizationId}/approvals`, {
    token: params.token,
    method: "POST",
    body: params.data,
  });
}

export async function approveApproval(
  params: OrgScoped & { approvalId: string },
): Promise<ApprovalRequestResponse> {
  return apiFetch<ApprovalRequestResponse>(
    `/organizations/${params.organizationId}/approvals/${params.approvalId}/approve`,
    { token: params.token, method: "POST" },
  );
}

export async function rejectApproval(
  params: OrgScoped & { approvalId: string },
): Promise<ApprovalRequestResponse> {
  return apiFetch<ApprovalRequestResponse>(
    `/organizations/${params.organizationId}/approvals/${params.approvalId}/reject`,
    { token: params.token, method: "POST" },
  );
}
