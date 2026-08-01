"use client";

import { Check, ChevronRight, Copy, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Badge, StatusBadge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { useTypewriter } from "@/hooks/use-typewriter";
import { AgentIcon } from "@/lib/agent-icons";
import { cn, humanizeEnumValue } from "@/lib/utils";
import type { AgentExecuteResponse } from "@/types/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: AgentExecuteResponse;
}

export function ChatBubble({
  message,
  organizationId,
}: {
  message: ChatMessage;
  organizationId: string;
}) {
  const isUser = message.role === "user";
  const { text: revealedText, done: revealDone } = useTypewriter(message.text, !isUser);

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-4 py-2 text-sm",
          isUser
            ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
            : "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.text}</p>
        ) : (
          <Markdown>{revealedText}</Markdown>
        )}

        {!isUser && revealDone && message.response ? (
          <div className="animate-fade-in-up mt-2 space-y-2">
            {message.response.tool_name ? <ToolCard response={message.response} /> : null}
            <WorkflowCard response={message.response} organizationId={organizationId} />
          </div>
        ) : null}

        {!isUser && revealDone ? <CopyButton text={message.text} /> : null}
      </div>
    </div>
  );
}

function ToolCard({ response }: { response: AgentExecuteResponse }) {
  const succeeded = response.workflow_status === "completed";
  return (
    <div
      className={cn(
        "rounded-md border p-2 text-xs",
        succeeded
          ? "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30"
          : "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Wrench className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="font-mono font-medium">{response.tool_name}()</span>
        {response.tool_result_code ? (
          <Badge tone={succeeded ? "success" : "danger"}>
            {humanizeEnumValue(response.tool_result_code)}
          </Badge>
        ) : null}
      </div>
      {response.tool_result_data ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-slate-500 select-none dark:text-slate-400">
            Result data
          </summary>
          <pre className="mt-1 overflow-x-auto rounded bg-white/60 p-2 dark:bg-black/20">
            {JSON.stringify(response.tool_result_data, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function WorkflowCard({
  response,
  organizationId,
}: {
  response: AgentExecuteResponse;
  organizationId: string;
}) {
  return (
    <Link
      href={`/org/${organizationId}/workflows/${response.workflow_id}`}
      className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white/60 px-2 py-1.5 text-xs transition-colors hover:bg-white dark:border-slate-700 dark:bg-slate-900/40 dark:hover:bg-slate-900"
    >
      <Badge tone="neutral">{humanizeEnumValue(response.decision_kind)}</Badge>
      <StatusBadge status={response.workflow_status} />
      {response.handled_by_agent ? (
        <span className="inline-flex items-center gap-1 opacity-80">
          <AgentIcon name={response.handled_by_agent} className="h-3.5 w-3.5" />
          {humanizeEnumValue(response.handled_by_agent)}
        </span>
      ) : null}
      <ChevronRight className="ml-auto h-3.5 w-3.5 shrink-0 opacity-50" aria-hidden />
    </Link>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="mt-1.5 inline-flex items-center gap-1 text-xs text-slate-400 transition-colors hover:text-slate-600 dark:hover:text-slate-300"
    >
      {copied ? <Check className="h-3 w-3" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
