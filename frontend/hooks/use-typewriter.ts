"use client";

import { useEffect, useState } from "react";

export interface UseTypewriterResult {
  /** The text revealed so far — equals `fullText` once `done` is true. */
  text: string;
  done: boolean;
}

const MIN_INTERVAL_MS = 16;
const TARGET_DURATION_MS = 700;

function splitIntoWords(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [];
}

/**
 * Progressively reveals `fullText`, word by word, as a client-side
 * animation over text that already arrived in full. `POST /agent/execute`
 * (see `services/agent.ts`) is a single atomic call — there is no real
 * token stream to animate — so this recreates the FEEL of one without
 * pretending the backend streams. Restarts whenever `fullText` changes.
 */
export function useTypewriter(fullText: string, enabled = true): UseTypewriterResult {
  const words = splitIntoWords(fullText);
  const [revealedWordCount, setRevealedWordCount] = useState(enabled ? 0 : words.length);

  // Reset the reveal count the moment `fullText`/`enabled` change, using
  // React's documented "storing information from previous renders"
  // pattern (https://react.dev/learn/you-might-not-need-an-effect) —
  // the same guarded during-render setState `hooks/use-workflow-event-stream.ts`
  // uses for its `trackedWorkflowId` — rather than an effect, since
  // `react-hooks/set-state-in-effect` flags a setState call that runs
  // synchronously in an effect body (as opposed to from the timer
  // callback below, which is the legitimate "subscribe to an external
  // system" case that rule allows).
  const [trackedFullText, setTrackedFullText] = useState(fullText);
  const [trackedEnabled, setTrackedEnabled] = useState(enabled);
  if (fullText !== trackedFullText || enabled !== trackedEnabled) {
    setTrackedFullText(fullText);
    setTrackedEnabled(enabled);
    setRevealedWordCount(enabled ? 0 : words.length);
  }

  useEffect(() => {
    if (!enabled || words.length === 0) return;

    const intervalMs = Math.max(MIN_INTERVAL_MS, TARGET_DURATION_MS / words.length);
    let count = 0;
    const id = setInterval(() => {
      count += 1;
      setRevealedWordCount(count);
      if (count >= words.length) clearInterval(id);
    }, intervalMs);

    return () => clearInterval(id);
  }, [fullText, enabled, words.length]);

  const text = words.slice(0, revealedWordCount).join("");
  return { text, done: revealedWordCount >= words.length };
}
