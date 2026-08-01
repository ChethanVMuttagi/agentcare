"use client";

import { useEffect, useState } from "react";

export interface UseStagedRevealResult<T> {
  /** The leading `revealedCount` items of `items`. */
  revealed: T[];
  revealedCount: number;
  done: boolean;
}

/**
 * Reveals `items` one at a time, `delayMs` apart — used to play back an
 * already-fetched, complete list (a workflow's timeline entries) as
 * though it were arriving live. See `features/demo/scenario-card.tsx`:
 * Demo Mode's underlying run is genuine, only the REVEAL is animated.
 * Restarts whenever `items` changes identity, so callers should pass a
 * stable array (state set once, not a fresh literal each render).
 */
export function useStagedReveal<T>(
  items: T[],
  options?: { enabled?: boolean; delayMs?: number },
): UseStagedRevealResult<T> {
  const enabled = options?.enabled ?? true;
  const delayMs = options?.delayMs ?? 260;
  const [revealedCount, setRevealedCount] = useState(enabled ? 0 : items.length);

  // Same guarded during-render reset as `hooks/use-typewriter.ts` — see
  // that hook's comment for why this isn't inside the effect below.
  const [trackedItems, setTrackedItems] = useState(items);
  const [trackedEnabled, setTrackedEnabled] = useState(enabled);
  if (items !== trackedItems || enabled !== trackedEnabled) {
    setTrackedItems(items);
    setTrackedEnabled(enabled);
    setRevealedCount(enabled ? 0 : items.length);
  }

  useEffect(() => {
    if (!enabled || items.length === 0) return;

    let count = 0;
    const id = setInterval(() => {
      count += 1;
      setRevealedCount(count);
      if (count >= items.length) clearInterval(id);
    }, delayMs);

    return () => clearInterval(id);
  }, [items, enabled, delayMs]);

  return {
    revealed: items.slice(0, revealedCount),
    revealedCount,
    done: revealedCount >= items.length,
  };
}
