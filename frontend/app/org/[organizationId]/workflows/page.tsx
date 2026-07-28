import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Pagination } from "@/components/ui/pagination";
import { WorkflowTable } from "@/features/workflows/workflow-table";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { listWorkflows } from "@/services/workflows";

export const metadata: Metadata = { title: "Workflows — AgentCare" };

const LIMIT = 20;

export default async function WorkflowsPage({
  params,
  searchParams,
}: {
  params: Promise<{ organizationId: string }>;
  searchParams: Promise<{ offset?: string }>;
}) {
  const { organizationId } = await params;
  const { offset: offsetParam } = await searchParams;
  const offset = Math.max(0, Number(offsetParam) || 0);
  const session = await requireSession();

  const result = await settle(
    listWorkflows({ token: session.token, organizationId, limit: LIMIT, offset }),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Workflows</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Administrative workflow runs started by staff or the AI assistant
        </p>
      </div>
      {!result.success ? (
        <ErrorState error={result.error} title="Couldn't load workflows" />
      ) : (
        <Card>
          <WorkflowTable organizationId={organizationId} workflows={result.data.workflows} />
          <Pagination
            basePath={`/org/${organizationId}/workflows`}
            limit={LIMIT}
            offset={offset}
            itemCount={result.data.workflows.length}
          />
        </Card>
      )}
    </div>
  );
}
