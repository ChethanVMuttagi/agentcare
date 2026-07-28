import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SummaryCard } from "@/features/dashboard/summary-card";
import { humanizeEnumValue } from "@/lib/utils";
import type { ApprovalRequestResponse } from "@/types/api";

export function ApprovalsSummary({
  organizationId,
  approvals,
  error,
}: {
  organizationId: string;
  approvals: ApprovalRequestResponse[] | null;
  error: unknown;
}) {
  return (
    <SummaryCard title="Pending Approvals" href={`/org/${organizationId}/approvals`}>
      {error ? (
        <div className="p-4">
          <ErrorState error={error} title="Couldn't load approvals" />
        </div>
      ) : !approvals || approvals.length === 0 ? (
        <div className="p-4">
          <EmptyState title="Nothing pending" description="Approval requests raised by the AI assistant will show up here." />
        </div>
      ) : (
        approvals.map((approval) => (
          <Link
            key={approval.id}
            href={`/org/${organizationId}/approvals/${approval.id}`}
            className="flex items-center justify-between px-5 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50"
          >
            <span className="text-slate-700 dark:text-slate-300">{humanizeEnumValue(approval.approval_type)}</span>
            <StatusBadge status={approval.status} />
          </Link>
        ))
      )}
    </SummaryCard>
  );
}
