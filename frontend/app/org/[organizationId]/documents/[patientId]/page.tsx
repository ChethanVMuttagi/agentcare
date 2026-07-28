import type { Metadata } from "next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/error-state";
import { DocumentTable } from "@/features/documents/document-table";
import { UploadDocumentForm } from "@/features/documents/upload-document-form";
import { requireSession } from "@/lib/require-session";
import { getRoleHint } from "@/lib/session";
import { settle } from "@/lib/utils";
import { listDocuments } from "@/services/documents";

export const metadata: Metadata = { title: "Patient Documents — AgentCare" };

export default async function PatientDocumentsPage({
  params,
}: {
  params: Promise<{ organizationId: string; patientId: string }>;
}) {
  const { organizationId, patientId } = await params;
  const session = await requireSession();
  const role = await getRoleHint();
  const canDelete = !role || role === "admin" || role === "staff";

  const result = await settle(
    listDocuments({ token: session.token, organizationId, patientId, limit: 100 }),
  );

  if (!result.success) {
    return <ErrorState error={result.error} title="Couldn't load documents" />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Documents</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">Files on record for this patient</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Upload a document</CardTitle>
        </CardHeader>
        <CardContent>
          <UploadDocumentForm organizationId={organizationId} patientId={patientId} />
        </CardContent>
      </Card>

      <Card>
        <DocumentTable
          organizationId={organizationId}
          patientId={patientId}
          documents={result.data.documents}
          canDelete={canDelete}
        />
      </Card>
    </div>
  );
}
