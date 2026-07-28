import type { Metadata } from "next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CreatePatientForm } from "@/features/patients/create-patient-form";

export const metadata: Metadata = { title: "Register Patient — AgentCare" };

export default async function NewPatientPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Register patient</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Add a new patient record to this organization</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Patient details</CardTitle>
        </CardHeader>
        <CardContent>
          <CreatePatientForm organizationId={organizationId} />
        </CardContent>
      </Card>
    </div>
  );
}
