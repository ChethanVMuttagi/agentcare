import type { Metadata } from "next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { BookAppointmentForm } from "@/features/appointments/book-appointment-form";
import { requireSession } from "@/lib/require-session";
import { settle } from "@/lib/utils";
import { listDepartments } from "@/services/departments";
import { listPractitioners } from "@/services/practitioners";

export const metadata: Metadata = { title: "Book Appointment — AgentCare" };

export default async function NewAppointmentPage({
  params,
  searchParams,
}: {
  params: Promise<{ organizationId: string }>;
  searchParams: Promise<{ patientId?: string }>;
}) {
  const { organizationId } = await params;
  const { patientId } = await searchParams;
  const session = await requireSession();

  const result = await settle(
    Promise.all([
      listDepartments({ token: session.token, organizationId, limit: 100 }),
      listPractitioners({ token: session.token, organizationId, limit: 100 }),
    ]),
  );

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load booking data" />;
  }

  const [{ departments }, { practitioners }] = result.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Book appointment</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Pick a department and practitioner, then find an open time
        </p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Appointment details</CardTitle>
        </CardHeader>
        <CardContent>
          <BookAppointmentForm
            organizationId={organizationId}
            departments={departments}
            practitioners={practitioners}
            defaultPatientId={patientId}
          />
        </CardContent>
      </Card>
    </div>
  );
}
