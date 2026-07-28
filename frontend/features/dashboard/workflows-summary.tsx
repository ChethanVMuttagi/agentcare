import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SummaryCard } from "@/features/dashboard/summary-card";
import { formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { WorkflowRunResponse } from "@/types/api";

export function WorkflowsSummary({
  organizationId,
  workflows,
  error,
}: {
  organizationId: string;
  workflows: WorkflowRunResponse[] | null;
  error: unknown;
}) {
  return (
    <SummaryCard title="Recent Workflows" href={`/org/${organizationId}/workflows`}>
      {error ? (
        <div className="p-4">
          <ErrorState error={error} title="Couldn't load workflows" />
        </div>
      ) : !workflows || workflows.length === 0 ? (
        <div className="p-4">
          <EmptyState title="No workflows yet" description="Runs started by staff or the AI assistant will show up here." />
        </div>
      ) : (
        workflows.map((workflow) => (
          <Link
            key={workflow.id}
            href={`/org/${organizationId}/workflows/${workflow.id}`}
            className="flex items-center justify-between px-5 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50"
          >
            <div>
              <p className="text-slate-700 dark:text-slate-300">{humanizeEnumValue(workflow.request_type)}</p>
              <p className="text-xs text-slate-400">{formatDateTime(workflow.created_at)}</p>
            </div>
            <StatusBadge status={workflow.status} />
          </Link>
        ))
      )}
    </SummaryCard>
  );
}
