import { StatusBadge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/table";
import { DeleteDocumentButton } from "@/features/documents/delete-document-button";
import { formatBytes, formatDateTime, humanizeEnumValue } from "@/lib/utils";
import type { DocumentResponse } from "@/types/api";

export function DocumentTable({
  organizationId,
  patientId,
  documents,
  canDelete,
}: {
  organizationId: string;
  patientId: string;
  documents: DocumentResponse[];
  canDelete: boolean;
}) {
  if (documents.length === 0) {
    return <EmptyState title="No documents yet" description="Files uploaded for this patient will show up here." />;
  }

  return (
    <Table>
      <TableHead>
        <TableRow>
          <TableHeaderCell>File</TableHeaderCell>
          <TableHeaderCell>Type</TableHeaderCell>
          <TableHeaderCell>Size</TableHeaderCell>
          <TableHeaderCell>Status</TableHeaderCell>
          <TableHeaderCell>Uploaded</TableHeaderCell>
          <TableHeaderCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {documents.map((document) => (
          <TableRow key={document.id}>
            <TableCell className="font-medium text-slate-900 dark:text-slate-100">
              {document.status === "available" ? (
                <a
                  href={`/api/backend/organizations/${organizationId}/patients/${patientId}/documents/${document.id}/download`}
                  className="hover:underline"
                >
                  {document.original_filename}
                </a>
              ) : (
                document.original_filename
              )}
            </TableCell>
            <TableCell>{humanizeEnumValue(document.document_type)}</TableCell>
            <TableCell>{formatBytes(document.size_bytes)}</TableCell>
            <TableCell>
              <StatusBadge status={document.status} />
            </TableCell>
            <TableCell>{formatDateTime(document.created_at)}</TableCell>
            <TableCell className="text-right">
              {canDelete ? (
                <DeleteDocumentButton
                  organizationId={organizationId}
                  patientId={patientId}
                  documentId={document.id}
                />
              ) : null}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
