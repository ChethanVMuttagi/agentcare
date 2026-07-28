import type { Metadata } from "next";

import { AssistantWorkspace } from "@/features/assistant/assistant-workspace";
import { requireSession } from "@/lib/require-session";

export const metadata: Metadata = { title: "AI Assistant — AgentCare" };

export default async function AssistantPage({
  params,
  searchParams,
}: {
  params: Promise<{ organizationId: string }>;
  searchParams: Promise<{ patientId?: string }>;
}) {
  const { organizationId } = await params;
  const { patientId } = await searchParams;
  await requireSession();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">AI Assistant</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Ask the assistant to book, reschedule, or route administrative requests
        </p>
      </div>
      <AssistantWorkspace organizationId={organizationId} defaultPatientId={patientId} />
    </div>
  );
}
