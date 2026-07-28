"use client";

import { useActionState, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { initialAssistantFormState, type AssistantFormState } from "@/features/assistant/action-state";
import { sendAssistantMessageAction } from "@/features/assistant/actions";
import { ChatBubble, type ChatMessage } from "@/features/assistant/chat-bubble";
import type { WorkflowRequestType } from "@/types/api";

const REQUEST_TYPE_OPTIONS: { value: WorkflowRequestType; label: string }[] = [
  { value: "administrative_routing", label: "Administrative routing" },
  { value: "appointment_booking", label: "Appointment booking" },
  { value: "appointment_rescheduling", label: "Appointment rescheduling" },
  { value: "appointment_cancellation", label: "Appointment cancellation" },
  { value: "document_collection", label: "Document collection" },
  { value: "follow_up", label: "Follow up" },
  { value: "reminder_delivery", label: "Reminder delivery" },
  { value: "patient_registration", label: "Patient registration" },
];

export function AssistantChat({
  organizationId,
  defaultPatientId,
  onWorkflowUpdate,
}: {
  organizationId: string;
  defaultPatientId?: string;
  /** Notified with the workflow id every time a response arrives — lets
   * a parent (see `features/assistant/assistant-workspace.tsx`) drive a
   * `LiveExecutionPanel` alongside the chat without this component
   * knowing anything about that panel. */
  onWorkflowUpdate?: (workflowId: string) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [workflowRunId, setWorkflowRunId] = useState<string | undefined>(undefined);
  const [state, formAction, pending] = useActionState(
    sendAssistantMessageAction.bind(null, organizationId),
    initialAssistantFormState,
  );
  const formRef = useRef<HTMLFormElement>(null);

  // Fold each new action result into the local message history during
  // render (React's documented "storing information from previous
  // renders" pattern — https://react.dev/learn/you-might-not-need-an-effect)
  // rather than in a useEffect, since `state` itself is already the
  // signal that a new turn happened; a guarded setState call here runs
  // once per distinct `state` and avoids an extra render pass.
  const [handledState, setHandledState] = useState<AssistantFormState>(state);
  if (state !== handledState) {
    setHandledState(state);
    if (state.requestText) {
      setMessages((previous) => [
        ...previous,
        { id: `user-${previous.length}`, role: "user", text: state.requestText! },
      ]);
    }
    if (state.response) {
      const response = state.response;
      setMessages((previous) => [
        ...previous,
        { id: `assistant-${previous.length}`, role: "assistant", text: response.message, response },
      ]);
      setWorkflowRunId(response.decision_kind === "clarification_required" ? response.workflow_id : undefined);
    }
  }

  // Resetting the form and notifying the parent of a new workflow id are
  // both effects on the outside world, not state updates — they belong
  // in an effect (which only runs after the response is reflected in
  // `handledState` above, once per turn).
  useEffect(() => {
    if (state.response) {
      formRef.current?.reset();
      onWorkflowUpdate?.(state.response.workflow_id);
    }
  }, [state, onWorkflowUpdate]);

  return (
    <div className="flex h-[70vh] flex-col rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Describe what you need — e.g. &ldquo;Book a follow-up with Dr. Lee next Tuesday
            afternoon&rdquo; — and the assistant will route it through the right workflow.
          </p>
        ) : (
          messages.map((message) => <ChatBubble key={message.id} message={message} />)
        )}
        {workflowRunId ? (
          <p className="text-xs text-slate-400">
            The assistant needs more information — your next message continues this request.
          </p>
        ) : null}
        {state.error ? (
          <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
            {state.error}
          </p>
        ) : null}
      </div>
      <form
        ref={formRef}
        action={formAction}
        className="space-y-2 border-t border-slate-200 p-4 dark:border-slate-800"
      >
        <input type="hidden" name="patient_id" value={defaultPatientId ?? ""} />
        <input type="hidden" name="workflow_run_id" value={workflowRunId ?? ""} />
        <Select name="request_type" defaultValue="administrative_routing" className="max-w-xs">
          {REQUEST_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Textarea name="request_text" rows={2} placeholder="Type a request…" required />
        <div className="flex justify-end">
          <Button type="submit" disabled={pending}>
            {pending ? "Sending…" : "Send"}
          </Button>
        </div>
      </form>
    </div>
  );
}
