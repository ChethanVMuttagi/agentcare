import Link from "next/link";

import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SummaryCard } from "@/features/dashboard/summary-card";
import { formatPersonName } from "@/lib/utils";
import type { PatientResponse } from "@/types/api";

export function PatientsSummary({
  organizationId,
  patients,
  error,
}: {
  organizationId: string;
  patients: PatientResponse[] | null;
  error: unknown;
}) {
  return (
    <SummaryCard title="Recently Added Patients" href={`/org/${organizationId}/patients`}>
      {error ? (
        <div className="p-4">
          <ErrorState error={error} title="Couldn't load patients" />
        </div>
      ) : !patients || patients.length === 0 ? (
        <div className="p-4">
          <EmptyState title="No patients yet" description="Registered patients will show up here." />
        </div>
      ) : (
        patients.map((patient) => (
          <Link
            key={patient.id}
            href={`/org/${organizationId}/patients/${patient.id}`}
            className="flex items-center justify-between px-5 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50"
          >
            <span className="text-slate-700 dark:text-slate-300">
              {formatPersonName(patient.first_name, patient.last_name)}
            </span>
            <span className="text-xs text-slate-400">{patient.patient_number}</span>
          </Link>
        ))
      )}
    </SummaryCard>
  );
}
