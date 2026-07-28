import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { SummaryCard } from "@/features/dashboard/summary-card";
import { formatDateTime } from "@/lib/utils";
import type { AppointmentResponse } from "@/types/api";

export function AppointmentsSummary({
  organizationId,
  appointments,
  error,
}: {
  organizationId: string;
  appointments: AppointmentResponse[] | null;
  error: unknown;
}) {
  return (
    <SummaryCard title="Recent Appointments" href={`/org/${organizationId}/appointments`}>
      {error ? (
        <div className="p-4">
          <ErrorState error={error} title="Couldn't load appointments" />
        </div>
      ) : !appointments || appointments.length === 0 ? (
        <div className="p-4">
          <EmptyState title="No appointments yet" description="Booked appointments will show up here." />
        </div>
      ) : (
        appointments.map((appointment) => (
          <Link
            key={appointment.id}
            href={`/org/${organizationId}/appointments/${appointment.id}`}
            className="flex items-center justify-between px-5 py-3 text-sm hover:bg-slate-50 dark:hover:bg-slate-800/50"
          >
            <span className="text-slate-700 dark:text-slate-300">{formatDateTime(appointment.start_at)}</span>
            <StatusBadge status={appointment.status} />
          </Link>
        ))
      )}
    </SummaryCard>
  );
}
