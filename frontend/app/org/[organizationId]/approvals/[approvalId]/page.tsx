import Link from "next/link";
import type { Metadata } from "next";

import { StatusBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { ApprovalDecisionButtons } from "@/features/approvals/approval-decision-buttons";
import { requireSession } from "@/lib/require-session";
import { getRoleHint } from "@/lib/session";
import { formatDateTime, humanizeEnumValue, settle } from "@/lib/utils";
import { getApproval } from "@/services/approvals";

export const metadata: Metadata = { title: "Approval — AgentCare" };

export default async function ApprovalDetailPage({
  params,
}: {
  params: Promise<{ organizationId: string; approvalId: string }>;
}) {
  const { organizationId, approvalId } = await params;
  const session = await requireSession();
  const role = await getRoleHint();
  const canDecide = !role || role === "admin" || role === "supervisor";

  const result = await settle(getApproval({ token: session.token, organizationId, approvalId }));

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load approval" />;
  }

  const approval = result.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          {humanizeEnumValue(approval.approval_type)}
        </h1>
        <StatusBadge status={approval.status} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <p className="text-sm text-slate-500 dark:text-slate-400">Reason</p>
            <p className="mt-1 text-sm text-slate-900 dark:text-slate-100">{approval.reason}</p>
          </div>
          <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Requested by</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{approval.requested_by_agent}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Expires</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{formatDateTime(approval.expires_at)}</dd>
            </div>
            {approval.approved_by_user ? (
              <div>
                <dt className="text-slate-500 dark:text-slate-400">Decided by</dt>
                <dd className="mt-1 text-slate-900 dark:text-slate-100">{approval.approved_by_user}</dd>
              </div>
            ) : null}
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Workflow</dt>
              <dd className="mt-1">
                <Link
                  href={`/org/${organizationId}/workflows/${approval.workflow_run_id}`}
                  className="text-slate-900 hover:underline dark:text-slate-100"
                >
                  View workflow
                </Link>
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {approval.status === "pending" && canDecide ? (
        <ApprovalDecisionButtons organizationId={organizationId} approvalId={approvalId} />
      ) : null}
    </div>
  );
}
