import type { Metadata } from "next";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AgentTopologyDiagram } from "@/features/architecture/agent-topology-diagram";
import { SystemDiagram } from "@/features/architecture/system-diagram";
import { requireSession } from "@/lib/require-session";

export const metadata: Metadata = { title: "Architecture — AgentCare" };

export default async function ArchitecturePage() {
  await requireSession();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">Architecture</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          How this application is actually put together — every claim on this page describes real,
          running code, not an aspirational diagram.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>System layers</CardTitle>
        </CardHeader>
        <CardContent>
          <SystemDiagram />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Multi-agent topology</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Every AI Assistant request starts with the Coordinator, which either answers directly, asks
            a clarifying question, or hands off to exactly one specialist below — each specialist can
            only call the tools listed under it (enforced in application code before the tool registry
            is ever consulted).
          </p>
          <AgentTopologyDiagram />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Event-sourced workflows</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-slate-600 dark:text-slate-300">
            <p>
              Every workflow run is three tables: a <code>WorkflowRun</code> (overall status), one or
              more <code>WorkflowStep</code>s (coordination, then specialist execution), and an
              append-only <code>WorkflowEvent</code> audit trail — nothing is ever updated or deleted
              from that trail once written.
            </p>
            <p>
              Each event carries a database-assigned, strictly monotonic <code>sequence</code> number —
              the ordering key the AI Timeline, Workflow Graph, and live stream all use instead of a
              timestamp, since two events created in the same request can share a timestamp at
              millisecond resolution but never a sequence number.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Real-time design</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-slate-600 dark:text-slate-300">
            <p>
              The Live Agent Execution panel subscribes to a Server-Sent Events endpoint that polls for
              new events every 1.5s (this codebase has no message bus — a new event is written from
              exactly one place, so polling the existing append-only table is the simplest correct
              mechanism) and pushes them to the browser as they appear.
            </p>
            <p>
              The browser&rsquo;s native <code>EventSource</code> handles reconnection itself, resuming
              from the last event it saw via the standard <code>Last-Event-ID</code> header — nothing
              is replayed twice, and nothing is lost across a dropped connection.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
