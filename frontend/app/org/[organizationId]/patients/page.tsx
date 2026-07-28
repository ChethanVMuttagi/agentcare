import Link from "next/link";
import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Pagination } from "@/components/ui/pagination";
import { PatientTable } from "@/features/patients/patient-table";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { listPatients } from "@/services/patients";

export const metadata: Metadata = { title: "Patients — AgentCare" };

const LIMIT = 20;

export default async function PatientsPage({
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
    listPatients({ token: session.token, organizationId, limit: LIMIT, offset }),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Patients</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Registered patients in this organization</p>
        </div>
        <Link
          href={`/org/${organizationId}/patients/new`}
          className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
        >
          Register patient
        </Link>
      </div>
      {!result.success ? (
        <ErrorState error={result.error} title="Couldn't load patients" />
      ) : (
        <Card>
          <PatientTable organizationId={organizationId} patients={result.data.patients} />
          <Pagination
            basePath={`/org/${organizationId}/patients`}
            limit={LIMIT}
            offset={offset}
            itemCount={result.data.patients.length}
          />
        </Card>
      )}
    </div>
  );
}
