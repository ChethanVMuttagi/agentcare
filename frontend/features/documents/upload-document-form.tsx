"use client";

import { useActionState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { initialDocumentFormState } from "@/features/documents/action-state";
import { uploadDocumentAction } from "@/features/documents/actions";
import type { DocumentType } from "@/types/api";

const DOCUMENT_TYPE_OPTIONS: { value: DocumentType; label: string }[] = [
  { value: "identity", label: "Identity" },
  { value: "insurance", label: "Insurance" },
  { value: "referral", label: "Referral" },
  { value: "consent", label: "Consent" },
  { value: "other", label: "Other" },
];

export function UploadDocumentForm({
  organizationId,
  patientId,
}: {
  organizationId: string;
  patientId: string;
}) {
  const [state, formAction, pending] = useActionState(
    uploadDocumentAction.bind(null, organizationId, patientId),
    initialDocumentFormState,
  );

  return (
    <form action={formAction} className="flex flex-wrap items-end gap-3">
      <div>
        <Label htmlFor="document_type">Document type</Label>
        <Select id="document_type" name="document_type" defaultValue="other">
          {DOCUMENT_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>
      <div>
        <Label htmlFor="file">File</Label>
        <input
          id="file"
          name="file"
          type="file"
          accept="application/pdf,image/jpeg,image/png"
          required
          className="block text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200 dark:text-slate-300 dark:file:bg-slate-800 dark:file:text-slate-200"
        />
      </div>
      <Button type="submit" disabled={pending}>
        {pending ? "Uploading…" : "Upload"}
      </Button>
      {state.error ? (
        <p className="w-full rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
          {state.error}
        </p>
      ) : null}
    </form>
  );
}
