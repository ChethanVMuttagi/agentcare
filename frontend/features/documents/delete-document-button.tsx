"use client";

import { useActionState } from "react";

import { initialDocumentFormState } from "@/features/documents/action-state";
import { deleteDocumentAction } from "@/features/documents/actions";

export function DeleteDocumentButton({
  organizationId,
  patientId,
  documentId,
}: {
  organizationId: string;
  patientId: string;
  documentId: string;
}) {
  const [state, formAction, pending] = useActionState(
    deleteDocumentAction.bind(null, organizationId, patientId, documentId),
    initialDocumentFormState,
  );

  return (
    <form action={formAction} className="inline-flex flex-col items-end gap-1">
      <button
        type="submit"
        disabled={pending}
        className="text-sm font-medium text-red-600 hover:underline disabled:opacity-60 dark:text-red-400"
      >
        {pending ? "Deleting…" : "Delete"}
      </button>
      {state.error ? <span className="text-xs text-red-600 dark:text-red-400">{state.error}</span> : null}
    </form>
  );
}
