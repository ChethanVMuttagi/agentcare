import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { ApprovalRequestResponse } from "@/types/api";

export function ApprovalTable({
  organizationId,
  approvals,
}: {
  organizationId: string;
  approvals: ApprovalRequestResponse[];
}) {
  if (approvals.length === 0) {
    return <EmptyState title="Nothing pending" description="Approval requests raised by the AI assistant will show up here." />;
  }

  return (
    <Table>
      <TableHead>
        <TableRow>
          <TableHeaderCell>Type</TableHeaderCell>
          <TableHeaderCell>Reason</TableHeaderCell>
          <TableHeaderCell>Status</TableHeaderCell>
          <TableHeaderCell>Requested</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {approvals.map((approval) => (
          <TableRow key={approval.id}>
            <TableCell>
              <Link
                href={`/org/${organizationId}/approvals/${approval.id}`}
                className="font-medium text-slate-900 hover:underline dark:text-slate-100"
              >
                {humanizeEnumValue(approval.approval_type)}
              </Link>
            </TableCell>
            <TableCell className="max-w-xs truncate">{approval.reason}</TableCell>
            <TableCell>
              <StatusBadge status={approval.status} />
            </TableCell>
            <TableCell>{formatDateTime(approval.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
