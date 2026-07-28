import "server-only";

import { apiFetch, apiFetchRaw } from "@/lib/api-fetch";
import type { DocumentListResponse, DocumentResponse, DocumentType } from "@/types/api";

interface PatientScoped {
  token: string;
  organizationId: string;
  patientId: string;
}

export async function listDocuments(
  params: PatientScoped & { limit?: number; offset?: number },
): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>(
    `/organizations/${params.organizationId}/patients/${params.patientId}/documents`,
    { token: params.token, searchParams: { limit: params.limit ?? 50, offset: params.offset ?? 0 } },
  );
}

export async function getDocument(
  params: PatientScoped & { documentId: string },
): Promise<DocumentResponse> {
  return apiFetch<DocumentResponse>(
    `/organizations/${params.organizationId}/patients/${params.patientId}/documents/${params.documentId}`,
    { token: params.token },
  );
}

export async function uploadDocument(
  params: PatientScoped & { documentType: DocumentType; file: File },
): Promise<DocumentResponse> {
  const formData = new FormData();
  formData.set("document_type", params.documentType);
  formData.set("file", params.file);
  return apiFetch<DocumentResponse>(
    `/organizations/${params.organizationId}/patients/${params.patientId}/documents`,
    { token: params.token, method: "POST", body: formData },
  );
}

export async function deleteDocument(
  params: PatientScoped & { documentId: string },
): Promise<DocumentResponse> {
  return apiFetch<DocumentResponse>(
    `/organizations/${params.organizationId}/patients/${params.patientId}/documents/${params.documentId}`,
    { token: params.token, method: "DELETE" },
  );
}

/** Returns the raw, still-streaming `Response` for a document download —
 * the caller (the `/api/backend` proxy route; see docs/FRONTEND.md
 * "Document Download") pipes it straight through to the browser with
 * the backend's own `Content-Disposition`/`Content-Type` headers. */
export async function downloadDocument(
  params: PatientScoped & { documentId: string },
): Promise<Response> {
  return apiFetchRaw(
    `/organizations/${params.organizationId}/patients/${params.patientId}/documents/${params.documentId}/download`,
    { token: params.token },
  );
}
