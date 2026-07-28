"use server";

import { revalidatePath } from "next/cache";

import type { DocumentFormState } from "@/features/documents/action-state";
import { describeError } from "@/lib/errors";
import { requireSession } from "@/lib/require-session";
import { deleteDocument, uploadDocument } from "@/services/documents";
import type { DocumentType } from "@/types/api";

export async function uploadDocumentAction(
  organizationId: string,
  patientId: string,
  _previousState: DocumentFormState,
  formData: FormData,
): Promise<DocumentFormState> {
  const session = await requireSession();

  const documentType = String(formData.get("document_type") ?? "") as DocumentType;
  const file = formData.get("file");

  if (!(file instanceof File) || file.size === 0) {
    return { error: "Choose a file to upload." };
  }
  if (!documentType) {
    return { error: "Choose a document type." };
  }

  try {
    await uploadDocument({ token: session.token, organizationId, patientId, documentType, file });
  } catch (error) {
    return { error: describeError(error) };
  }

  revalidatePath(`/org/${organizationId}/documents/${patientId}`);
  return { error: null };
}

export async function deleteDocumentAction(
  organizationId: string,
  patientId: string,
  documentId: string,
  _previousState: DocumentFormState,
): Promise<DocumentFormState> {
  const session = await requireSession();

  try {
    await deleteDocument({ token: session.token, organizationId, patientId, documentId });
  } catch (error) {
    return { error: describeError(error) };
  }

  revalidatePath(`/org/${organizationId}/documents/${patientId}`);
  return { error: null };
}
