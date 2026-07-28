import "server-only";

import { apiFetch } from "@/lib/api-fetch";
import type { AnalyticsSummaryResponse } from "@/types/api";

interface OrgScoped {
  token: string;
  organizationId: string;
}

export async function getAnalyticsSummary(params: OrgScoped): Promise<AnalyticsSummaryResponse> {
  return apiFetch<AnalyticsSummaryResponse>(
    `/organizations/${params.organizationId}/analytics/summary`,
    { token: params.token },
  );
}
