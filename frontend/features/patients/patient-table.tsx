import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { formatDate, formatPersonName } from "@/lib/utils";
import type { PatientResponse } from "@/types/api";

export function PatientTable({
  organizationId,
  patients,
}: {
  organizationId: string;
  patients: PatientResponse[];
}) {
  if (patients.length === 0) {
    return (
      <EmptyState
        title="No patients found"
        description="Register a patient to see them listed here."
      />
    );
  }

  return (
    <Table>
      <TableHead>
        <TableRow>
          <TableHeaderCell>Name</TableHeaderCell>
          <TableHeaderCell>Patient #</TableHeaderCell>
          <TableHeaderCell>Date of birth</TableHeaderCell>
          <TableHeaderCell>Status</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {patients.map((patient) => (
          <TableRow key={patient.id}>
            <TableCell>
              <Link
                href={`/org/${organizationId}/patients/${patient.id}`}
                className="font-medium text-slate-900 hover:underline dark:text-slate-100"
              >
                {formatPersonName(patient.first_name, patient.last_name)}
              </Link>
            </TableCell>
            <TableCell>{patient.patient_number}</TableCell>
            <TableCell>{formatDate(patient.date_of_birth)}</TableCell>
            <TableCell>{patient.is_active ? "Active" : "Inactive"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
