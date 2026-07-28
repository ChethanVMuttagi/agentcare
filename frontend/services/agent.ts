import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type { AgentExecuteRequest, AgentExecuteResponse } from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function executeAgentRequest(
  params: OrgScoped & { data: AgentExecuteRequest },
): Promise<AgentExecuteResponse> {
  return apiFetch<AgentExecuteResponse>(`/organizations/${params.organizationId}/agent/execute`, {
    token: params.token,
    method: "POST",
    body: params.data,
  });
}
