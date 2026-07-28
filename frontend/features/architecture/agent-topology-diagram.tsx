const SPECIALISTS = [
  { name: "Scheduling", tools: ["check_availability", "book_appointment", "reschedule_appointment"] },
  { name: "Document", tools: ["list_patient_documents"] },
  { name: "Routing", tools: ["resolve_department"] },
];

/** The fixed multi-agent topology every workflow run traverses some
 * subset of — mirrors `app.ai.agents.definitions` on the backend, the
 * single source of truth this documents (no separate "architecture"
 * data model exists; this is deliberately just a static rendering of a
 * fact that's already true of the running system). Compare with
 * `features/workflows/workflow-graph.tsx`, which draws the SAME shape
 * but highlights which nodes one specific workflow actually visited. */
export function AgentTopologyDiagram() {
  return (
    <div className="overflow-x-auto">
      <div className="flex min-w-max items-start gap-6 p-2">
        <div className="flex h-14 w-36 shrink-0 items-center justify-center rounded-lg border border-slate-900 bg-slate-900 text-sm font-medium text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900">
          Coordinator
        </div>
        <div className="flex flex-col gap-4">
          {SPECIALISTS.map((agent) => (
            <div key={agent.name} className="flex flex-wrap items-center gap-3">
              <div className="flex h-12 w-32 shrink-0 items-center justify-center rounded-lg border border-slate-300 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-200">
                {agent.name}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {agent.tools.map((tool) => (
                  <span
                    key={tool}
                    className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                  >
                    {tool}()
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
