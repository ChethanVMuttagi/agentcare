import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type {
  PatientCreate,
  WorkflowEventListResponse,
  WorkflowRunListResponse,
  WorkflowRunResponse,
  WorkflowStepListResponse,
  WorkflowTimelineResponse,
} from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function listWorkflows(
  params: OrgScoped & { limit?: number; offset?: number },
): Promise<WorkflowRunListResponse> {
  return apiFetch<WorkflowRunListResponse>(`/organizations/${params.organizationId}/workflows`, {
    token: params.token,
    searchParams: { limit: params.limit ?? 50, offset: params.offset ?? 0 },
  });
}

export async function getWorkflow(
  params: OrgScoped & { workflowId: string },
): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(
    `/organizations/${params.organizationId}/workflows/${params.workflowId}`,
    { token: params.token },
  );
}

export async function listWorkflowSteps(
  params: OrgScoped & { workflowId: string },
): Promise<WorkflowStepListResponse> {
  return apiFetch<WorkflowStepListResponse>(
    `/organizations/${params.organizationId}/workflows/${params.workflowId}/steps`,
    { token: params.token },
  );
}

export async function listWorkflowEvents(
  params: OrgScoped & { workflowId: string },
): Promise<WorkflowEventListResponse> {
  return apiFetch<WorkflowEventListResponse>(
    `/organizations/${params.organizationId}/workflows/${params.workflowId}/events`,
    { token: params.token },
  );
}

export async function getWorkflowTimeline(
  params: OrgScoped & { workflowId: string },
): Promise<WorkflowTimelineResponse> {
  return apiFetch<WorkflowTimelineResponse>(
    `/organizations/${params.organizationId}/workflows/${params.workflowId}/timeline`,
    { token: params.token },
  );
}

export async function cancelWorkflow(
  params: OrgScoped & { workflowId: string },
): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(
    `/organizations/${params.organizationId}/workflows/${params.workflowId}/cancel`,
    { token: params.token, method: "POST" },
  );
}

export async function startPatientRegistration(
  params: OrgScoped & { data: PatientCreate },
): Promise<WorkflowRunResponse> {
  return apiFetch<WorkflowRunResponse>(
    `/organizations/${params.organizationId}/workflows/patient-registrations`,
    { token: params.token, method: "POST", body: params.data },
  );
}
