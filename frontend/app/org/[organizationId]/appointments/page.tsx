import Link from "next/link";
import type { Metadata } from "next";

import { Card } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { Pagination } from "@/components/ui/pagination";
import { AppointmentTable } from "@/features/appointments/appointment-table";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { listAppointments } from "@/services/appointments";

export const metadata: Metadata = { title: "Appointments — AgentCare" };

const LIMIT = 20;

export default async function AppointmentsPage({
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
    listAppointments({ token: session.token, organizationId, limit: LIMIT, offset }),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Appointments</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Scheduled appointments in this organization</p>
        </div>
        <Link
          href={`/org/${organizationId}/appointments/new`}
          className="inline-flex h-10 items-center justify-center rounded-md bg-slate-900 px-4 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
        >
          Book appointment
        </Link>
      </div>
      {!result.success ? (
        <ErrorState error={result.error} title="Couldn't load appointments" />
      ) : (
        <Card>
          <AppointmentTable organizationId={organizationId} appointments={result.data.appointments} />
          <Pagination
            basePath={`/org/${organizationId}/appointments`}
            limit={LIMIT}
            offset={offset}
            itemCount={result.data.appointments.length}
          />
        </Card>
      )}
    </div>
  );
}
