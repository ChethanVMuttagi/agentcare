const LAYERS = [
  {
    title: "Browser",
    detail:
      "Client Components handle interactivity; the session JWT lives only in an httpOnly cookie and is never readable by browser JS. EventSource (SSE) and the availability/chat lookups are the only client-initiated backend calls.",
  },
  {
    title: "Next.js server",
    detail:
      "Server Components and Server Actions call the FastAPI backend directly (no CORS needed — server-to-server). The /api/backend/[...path] route proxies the few genuinely client-driven calls, attaching the bearer token itself so the browser never sees it.",
  },
  {
    title: "FastAPI backend",
    detail:
      "REST API, JWT auth + role-based access control, and the AI orchestration layer (Coordinator + 3 specialist agents, a per-agent tool allowlist, and a two-layer safety screen).",
  },
  {
    title: "PostgreSQL",
    detail:
      "Durable state for every resource, plus the append-only WorkflowEvent audit trail the AI Trace Viewer and Analytics Dashboard both read from.",
  },
];

export function SystemDiagram() {
  return (
    <div className="space-y-1">
      {LAYERS.map((layer, index) => (
        <div key={layer.title}>
          <div className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
            <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{layer.title}</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{layer.detail}</p>
          </div>
          {index < LAYERS.length - 1 ? (
            <div className="flex justify-center py-1">
              <svg viewBox="0 0 24 24" className="h-5 w-5 text-slate-300 dark:text-slate-700" fill="currentColor">
                <path d="M12 16l-6-6h12z" />
              </svg>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
