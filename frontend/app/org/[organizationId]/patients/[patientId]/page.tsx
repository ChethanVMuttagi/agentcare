import Link from "next/link";
import type { Metadata } from "next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { requireSession } from "@/lib/require-session";
import { formatDate, formatDateTime, formatPersonName, settle } from "@/lib/utils";
import { getPatient } from "@/services/patients";

export const metadata: Metadata = { title: "Patient — AgentCare" };

export default async function PatientDetailPage({
  params,
}: {
  params: Promise<{ organizationId: string; patientId: string }>;
}) {
  const { organizationId, patientId } = await params;
  const session = await requireSession();

  const result = await settle(getPatient({ token: session.token, organizationId, patientId }));

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load patient" />;
  }

  const patient = result.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          {formatPersonName(patient.first_name, patient.last_name)}
        </h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Patient #{patient.patient_number}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Date of birth</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{formatDate(patient.date_of_birth)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Status</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">
                {patient.is_active ? "Active" : "Inactive"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Registered</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{formatDateTime(patient.created_at)}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <div className="flex flex-wrap gap-3">
        <Link
          href={`/org/${organizationId}/appointments/new?patientId=${patient.id}`}
          className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
        >
          Book appointment
        </Link>
        <Link
          href={`/org/${organizationId}/documents?patientId=${patient.id}`}
          className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          View documents
        </Link>
      </div>
    </div>
  );
}
