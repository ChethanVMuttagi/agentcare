import { cn } from "@/lib/utils";
import type { WorkflowStreamEntry } from "@/types/api";

const AGENT_LABELS: Record<string, string> = {
  coordinator: "Coordinator",
  scheduling: "Scheduling",
  document: "Document",
  routing: "Routing",
};

const SPECIALIST_AGENTS = ["scheduling", "document", "routing"] as const;

interface GraphState {
  visitedAgents: Set<string>;
  currentAgent: string | null;
  toolByAgent: Map<string, string>;
  isRunning: boolean;
}

function computeGraphState(entries: WorkflowStreamEntry[], isRunning: boolean): GraphState {
  const visitedAgents = new Set<string>();
  const toolByAgent = new Map<string, string>();
  let currentAgent: string | null = null;

  for (const entry of entries) {
    if (entry.event_type === "agent_handoff") {
      const toAgent = entry.safe_metadata?.to_agent;
      const fromAgent = entry.safe_metadata?.from_agent;
      if (typeof fromAgent === "string") visitedAgents.add(fromAgent);
      if (typeof toAgent === "string") {
        visitedAgents.add(toAgent);
        currentAgent = toAgent;
      }
    } else if (entry.step_agent_name) {
      visitedAgents.add(entry.step_agent_name);
      currentAgent = entry.step_agent_name;
    }

    if (entry.event_type === "tool_invoked") {
      const toolName = entry.safe_metadata?.tool_name;
      const agent = entry.step_agent_name ?? currentAgent;
      if (typeof toolName === "string" && agent) {
        toolByAgent.set(agent, toolName);
      }
    }
  }

  if (visitedAgents.size === 0) {
    visitedAgents.add("coordinator");
    currentAgent = "coordinator";
  }

  return { visitedAgents, currentAgent, toolByAgent, isRunning };
}

function AgentNode({
  name,
  visited,
  active,
  isRunning,
}: {
  name: string;
  visited: boolean;
  active: boolean;
  isRunning: boolean;
}) {
  return (
    <div
      className={cn(
        "relative flex h-14 w-32 shrink-0 items-center justify-center rounded-lg border px-3 text-center text-sm font-medium transition-colors",
        visited
          ? "border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900"
          : "border-dashed border-slate-300 text-slate-400 dark:border-slate-700 dark:text-slate-600",
      )}
    >
      {active && isRunning ? (
        <span className="absolute -top-1 -right-1 flex h-3 w-3">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500" />
        </span>
      ) : null}
      {AGENT_LABELS[name] ?? name}
    </div>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div
      className={cn(
        "h-px w-8 shrink-0 sm:w-12",
        active ? "bg-slate-900 dark:bg-slate-100" : "bg-slate-200 dark:bg-slate-800",
      )}
    />
  );
}

/**
 * A small, dependency-free visualization of AgentCare's fixed multi-agent
 * topology (Coordinator + 3 specialists — see `app.ai.agents.definitions`
 * on the backend, the single source of truth this mirrors) with which
 * nodes/edges this SPECIFIC workflow actually traversed highlighted,
 * derived purely from its timeline entries — no separate "graph" data
 * ever comes from the backend, since the topology itself never changes
 * and the traversal is fully reconstructible from the existing event
 * stream (see `app.services.workflow.WorkflowService.record_agent_handoff`/
 * `record_tool_invocation`).
 */
export function WorkflowGraph({
  entries,
  isRunning,
}: {
  entries: WorkflowStreamEntry[];
  isRunning: boolean;
}) {
  const state = computeGraphState(entries, isRunning);

  return (
    <div className="overflow-x-auto">
      <div className="flex min-w-max items-center gap-0 p-2">
        <AgentNode
          name="coordinator"
          visited={state.visitedAgents.has("coordinator")}
          active={state.currentAgent === "coordinator"}
          isRunning={state.isRunning}
        />
        <Connector active={state.visitedAgents.size > 1} />
        <div className="flex flex-col gap-3">
          {SPECIALIST_AGENTS.map((agent) => {
            const visited = state.visitedAgents.has(agent);
            const tool = state.toolByAgent.get(agent);
            return (
              <div key={agent} className="flex items-center gap-0">
                <AgentNode
                  name={agent}
                  visited={visited}
                  active={state.currentAgent === agent}
                  isRunning={state.isRunning}
                />
                {tool ? (
                  <>
                    <Connector active={visited} />
                    <div className="flex h-10 shrink-0 items-center rounded-md border border-emerald-300 bg-emerald-50 px-3 text-xs font-medium text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                      {tool}()
                    </div>
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
