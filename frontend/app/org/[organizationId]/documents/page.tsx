import Link from "next/link";
import { redirect } from "next/navigation";
import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { requireSession } from "@/lib/require-session";
import { getRoleHint } from "@/lib/session";
import { formatPersonName, settle } from "@/lib/utils";
import { getOwnPatient, listPatients } from "@/services/patients";

export const metadata: Metadata = { title: "Documents — AgentCare" };

export default async function DocumentsIndexPage({
  params,
  searchParams,
}: {
  params: Promise<{ organizationId: string }>;
  searchParams: Promise<{ patientId?: string }>;
}) {
  const { organizationId } = await params;
  const { patientId } = await searchParams;
  const session = await requireSession();
  const role = await getRoleHint();

  if (patientId) {
    redirect(`/org/${organizationId}/documents/${patientId}`);
  }

  if (role === "patient") {
    const ownResult = await settle(getOwnPatient({ token: session.token, organizationId }));
    if (!ownResult.success) {
      return <ErrorState error={ownResult.error} title="Couldn't load your patient record" />;
    }
    redirect(`/org/${organizationId}/documents/${ownResult.data.id}`);
  }

  const result = await settle(listPatients({ token: session.token, organizationId, limit: 50 }));

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load patients" />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Documents</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Select a patient to view or upload documents</p>
      </div>
      <Card>
        {result.data.patients.length === 0 ? (
          <p className="p-5 text-sm text-slate-500 dark:text-slate-400">No patients registered yet.</p>
        ) : (
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {result.data.patients.map((patient) => (
              <Link
                key={patient.id}
                href={`/org/${organizationId}/documents/${patient.id}`}
                className="flex items-center justify-between px-5 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50"
              >
                <span className="text-slate-700 dark:text-slate-300">
                  {formatPersonName(patient.first_name, patient.last_name)}
                </span>
                <span className="text-xs text-slate-400">{patient.patient_number}</span>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
