import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/utils";
import type { AppointmentResponse } from "@/types/api";

export function AppointmentTable({
  organizationId,
  appointments,
}: {
  organizationId: string;
  appointments: AppointmentResponse[];
}) {
  if (appointments.length === 0) {
    return <EmptyState title="No appointments found" description="Booked appointments will show up here." />;
  }

  return (
    <Table>
      <TableHead>
        <TableRow>
          <TableHeaderCell>Start</TableHeaderCell>
          <TableHeaderCell>End</TableHeaderCell>
          <TableHeaderCell>Status</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {appointments.map((appointment) => (
          <TableRow key={appointment.id}>
            <TableCell>
              <Link
                href={`/org/${organizationId}/appointments/${appointment.id}`}
                className="font-medium text-slate-900 hover:underline dark:text-slate-100"
              >
                {formatDateTime(appointment.start_at)}
              </Link>
            </TableCell>
            <TableCell>{formatDateTime(appointment.end_at)}</TableCell>
            <TableCell>
              <StatusBadge status={appointment.status} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
