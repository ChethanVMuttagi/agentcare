import type { Metadata } from "next";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { CancelForm } from "@/features/appointments/cancel-form";
import { RescheduleForm } from "@/features/appointments/reschedule-form";
import { requireSession } from "@/lib/require-session";
import { formatDateTime, settle } from "@/lib/utils";
import { getAppointment } from "@/services/appointments";

export const metadata: Metadata = { title: "Appointment — AgentCare" };

export default async function AppointmentDetailPage({
  params,
}: {
  params: Promise<{ organizationId: string; appointmentId: string }>;
}) {
  const { organizationId, appointmentId } = await params;
  const session = await requireSession();

  const result = await settle(
    getAppointment({ token: session.token, organizationId, appointmentId }),
  );

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load appointment" />;
  }

  const appointment = result.data;
  const durationMinutes = Math.round(
    (new Date(appointment.end_at).getTime() - new Date(appointment.start_at).getTime()) / 60000,
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          {formatDateTime(appointment.start_at)}
        </h1>
        <StatusBadge status={appointment.status} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Start</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{formatDateTime(appointment.start_at)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">End</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{formatDateTime(appointment.end_at)}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Practitioner</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{appointment.practitioner_id}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">Department</dt>
              <dd className="mt-1 text-slate-900 dark:text-slate-100">{appointment.department_id}</dd>
            </div>
            {appointment.cancellation_reason ? (
              <div className="sm:col-span-2">
                <dt className="text-slate-500 dark:text-slate-400">Cancellation reason</dt>
                <dd className="mt-1 text-slate-900 dark:text-slate-100">{appointment.cancellation_reason}</dd>
              </div>
            ) : null}
          </dl>
        </CardContent>
      </Card>

      {appointment.status === "booked" ? (
        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Reschedule</CardTitle>
            </CardHeader>
            <CardContent>
              <RescheduleForm
                organizationId={organizationId}
                appointmentId={appointmentId}
                currentDurationMinutes={durationMinutes}
              />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Cancel</CardTitle>
            </CardHeader>
            <CardContent>
              <CancelForm organizationId={organizationId} appointmentId={appointmentId} />
            </CardContent>
          </Card>
        </div>
      ) : (
        <Badge tone="neutral">This appointment can no longer be modified.</Badge>
      )}
    </div>
  );
}
