import type { Metadata } from "next";

import { AppointmentsSummary } from "@/features/dashboard/appointments-summary";
import { ApprovalsSummary } from "@/features/dashboard/approvals-summary";
import { PatientsSummary } from "@/features/dashboard/patients-summary";
import { WorkflowsSummary } from "@/features/dashboard/workflows-summary";
import { requireSession } from "@/lib/require-session";
import { getRoleHint } from "@/lib/session";
import { settle } from "@/lib/utils";
import { listAppointments } from "@/services/appointments";
import { listApprovals } from "@/services/approvals";
import { listPatients } from "@/services/patients";
import { listWorkflows } from "@/services/workflows";

export const metadata: Metadata = { title: "Dashboard — AgentCare" };

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;
  const session = await requireSession();
  const role = await getRoleHint();

  const canSeePatients = !role || role === "admin" || role === "staff";
  const canSeeApprovals = !role || role === "admin" || role === "staff" || role === "supervisor";

  const [appointments, workflows, approvals, patients] = await Promise.all([
    settle(listAppointments({ token: session.token, organizationId, limit: 5 })),
    settle(listWorkflows({ token: session.token, organizationId, limit: 5 })),
    canSeeApprovals
      ? settle(listApprovals({ token: session.token, organizationId, limit: 5 }))
      : Promise.resolve(null),
    canSeePatients
      ? settle(listPatients({ token: session.token, organizationId, limit: 5 }))
      : Promise.resolve(null),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Organization {organizationId}</p>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <AppointmentsSummary
          organizationId={organizationId}
          appointments={appointments.success ? appointments.data.appointments : null}
          error={appointments.success ? null : appointments.error}
        />
        <WorkflowsSummary
          organizationId={organizationId}
          workflows={workflows.success ? workflows.data.workflows : null}
          error={workflows.success ? null : workflows.error}
        />
        {approvals ? (
          <ApprovalsSummary
            organizationId={organizationId}
            approvals={approvals.success ? approvals.data.approvals : null}
            error={approvals.success ? null : approvals.error}
          />
        ) : null}
        {patients ? (
          <PatientsSummary
            organizationId={organizationId}
            patients={patients.success ? patients.data.patients : null}
            error={patients.success ? null : patients.error}
          />
        ) : null}
      </div>
    </div>
  );
}
