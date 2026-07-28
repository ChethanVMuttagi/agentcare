import type { Metadata } from "next";

import { ScenarioCard } from "@/features/demo/scenario-card";
import { requireSession } from "@/lib/require-session";
import type { WorkflowRequestType } from "@/types/api";

export const metadata: Metadata = { title: "Demo Mode — AgentCare" };

const SCENARIOS: {
  title: string;
  description: string;
  requestType: WorkflowRequestType;
  requestText: string;
}[] = [
  {
    title: "Book an appointment",
    description: "Coordinator hands off to the Scheduling agent, which checks availability and books.",
    requestType: "appointment_booking",
    requestText: "Book a follow-up appointment with the next available practitioner.",
  },
  {
    title: "Reschedule an appointment",
    description: "Scheduling agent looks up the existing booking and moves it.",
    requestType: "appointment_rescheduling",
    requestText: "Reschedule my upcoming appointment to next Friday afternoon.",
  },
  {
    title: "Cancel an appointment",
    description: "Scheduling agent cancels a booking on request.",
    requestType: "appointment_cancellation",
    requestText: "Cancel my appointment scheduled for next week.",
  },
  {
    title: "Document collection",
    description: "Coordinator hands off to the Document agent to check what's on file.",
    requestType: "document_collection",
    requestText: "I need to submit my insurance documents — what do you already have on file?",
  },
  {
    title: "Administrative routing",
    description: "A general request the Coordinator routes to the right specialist itself.",
    requestType: "administrative_routing",
    requestText: "Can you help me update my contact information?",
  },
  {
    title: "Ambiguous request",
    description: "Deliberately vague — likely triggers a clarification pause instead of a tool call.",
    requestType: "administrative_routing",
    requestText: "I need help with something for my next visit.",
  },
];

export default async function DemoPage({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const { organizationId } = await params;
  await requireSession();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Demo Mode</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Run a seeded scenario through the real AI Assistant and watch the multi-agent trace live —
          nothing here is scripted, each run is a genuine orchestration.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SCENARIOS.map((scenario) => (
          <ScenarioCard key={scenario.title} organizationId={organizationId} {...scenario} />
        ))}
      </div>
    </div>
  );
}
