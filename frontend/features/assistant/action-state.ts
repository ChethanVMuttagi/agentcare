import type { AgentExecuteResponse } from "@/types/api";

export interface AssistantFormState {
  response: AgentExecuteResponse | null;
  error: string | null;
  /** Echoes back what the user just sent so the chat UI can render the
   * user's turn even though this state only carries the latest exchange
   * — the conversation history itself lives in client state (see
   * `features/assistant/assistant-chat.tsx`). */
  requestText: string | null;
}

export const initialAssistantFormState: AssistantFormState = {
  response: null,
  error: null,
  requestText: null,
};
