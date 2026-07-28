import { Badge, StatusBadge } from "@/components/ui/badge";
import { cn, humanizeEnumValue } from "@/lib/utils";
import type { AgentExecuteResponse } from "@/types/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: AgentExecuteResponse;
}

export function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-2 text-sm whitespace-pre-wrap",
          isUser
            ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
            : "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
        )}
      >
        <p>{message.text}</p>
        {message.response ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{humanizeEnumValue(message.response.decision_kind)}</Badge>
            <StatusBadge status={message.response.workflow_status} />
            {message.response.handled_by_agent ? (
              <span className="text-xs opacity-70">via {message.response.handled_by_agent}</span>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
