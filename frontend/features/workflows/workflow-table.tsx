import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { WorkflowRunResponse } from "@/types/api";

export function WorkflowTable({
  organizationId,
  workflows,
}: {
  organizationId: string;
  workflows: WorkflowRunResponse[];
}) {
  if (workflows.length === 0) {
    return (
      <EmptyState
        title="No workflows found"
        description="Runs started by staff or the AI assistant will show up here."
      />
    );
  }

  return (
    <Table>
      <TableHead>
        <TableRow>
          <TableHeaderCell>Request type</TableHeaderCell>
          <TableHeaderCell>Status</TableHeaderCell>
          <TableHeaderCell>Started</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {workflows.map((workflow) => (
          <TableRow key={workflow.id}>
            <TableCell>
              <Link
                href={`/org/${organizationId}/workflows/${workflow.id}`}
                className="font-medium text-slate-900 hover:underline dark:text-slate-100"
              >
                {humanizeEnumValue(workflow.request_type)}
              </Link>
            </TableCell>
            <TableCell>
              <StatusBadge status={workflow.status} />
            </TableCell>
            <TableCell>{formatDateTime(workflow.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
