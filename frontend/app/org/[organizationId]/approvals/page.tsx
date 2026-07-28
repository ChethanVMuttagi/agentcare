import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Pagination } from "@/components/ui/pagination";
import { ApprovalTable } from "@/features/approvals/approval-table";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { listApprovals } from "@/services/approvals";

export const metadata: Metadata = { title: "Approvals — AgentCare" };

const LIMIT = 20;

export default async function ApprovalsPage({
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
    listApprovals({ token: session.token, organizationId, limit: LIMIT, offset }),
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Approval Center</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Pending approval requests raised by the AI assistant</p>
      </div>
      {!result.success ? (
        <ErrorState error={result.error} title="Couldn't load approvals" />
      ) : (
        <Card>
          <ApprovalTable organizationId={organizationId} approvals={result.data.approvals} />
          <Pagination
            basePath={`/org/${organizationId}/approvals`}
            limit={LIMIT}
            offset={offset}
            itemCount={result.data.approvals.length}
          />
        </Card>
      )}
    </div>
  );
}
