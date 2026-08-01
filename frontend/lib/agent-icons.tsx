import {
  Bot,
  CalendarClock,
  ClipboardCheck,
  Clock3,
  FileText,
  GitBranch,
  Route,
  Wrench,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import type { EventCategory } from "@/lib/event-category";

/** Agent name (see `app.ai.agents.definitions` on the backend, the single
 * source of truth this mirrors — the same fixed topology
 * `features/workflows/workflow-graph.tsx` already hard-codes) -> icon. */
export const AGENT_ICONS: Record<string, LucideIcon> = {
  coordinator: Bot,
  scheduling: CalendarClock,
  document: FileText,
  routing: Route,
};

export function AgentIcon({ name, className }: { name: string; className?: string }) {
  const Icon = AGENT_ICONS[name] ?? Bot;
  return <Icon className={className} aria-hidden />;
}

export const EVENT_CATEGORY_ICONS: Record<EventCategory, LucideIcon> = {
  agent: GitBranch,
  tool: Wrench,
  approval: ClipboardCheck,
  failure: XCircle,
  lifecycle: Clock3,
};

export function EventCategoryIcon({
  category,
  className,
}: {
  category: EventCategory;
  className?: string;
}) {
  const Icon = EVENT_CATEGORY_ICONS[category];
  return <Icon className={className} aria-hidden />;
}
