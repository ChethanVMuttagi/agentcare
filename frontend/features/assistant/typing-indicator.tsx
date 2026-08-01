/** The assistant's "thinking" bubble — shown while `POST /agent/execute`
 * is in flight (see `features/assistant/assistant-chat.tsx`). A real wait
 * signal, not an animation over data that already arrived. */
export function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1.5 rounded-lg bg-slate-100 px-4 py-3 dark:bg-slate-800">
        <span
          className="typing-dot h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500"
          style={{ animationDelay: "0ms" }}
        />
        <span
          className="typing-dot h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500"
          style={{ animationDelay: "150ms" }}
        />
        <span
          className="typing-dot h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500"
          style={{ animationDelay: "300ms" }}
        />
      </div>
    </div>
  );
}
