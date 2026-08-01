// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useStagedReveal } from "@/hooks/use-staged-reveal";

describe("useStagedReveal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("reveals items one at a time, delayMs apart", () => {
    const items = ["a", "b", "c"];
    const { result } = renderHook(() => useStagedReveal(items, { delayMs: 100 }));
    expect(result.current.revealed).toEqual([]);
    expect(result.current.done).toBe(false);

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.revealed).toEqual(["a"]);

    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current.revealed).toEqual(["a", "b", "c"]);
    expect(result.current.done).toBe(true);
  });

  it("reveals everything immediately when disabled", () => {
    const items = [1, 2, 3];
    const { result } = renderHook(() => useStagedReveal(items, { enabled: false }));
    expect(result.current.revealed).toEqual([1, 2, 3]);
    expect(result.current.done).toBe(true);
  });

  it("treats an empty array as immediately done", () => {
    const { result } = renderHook(() => useStagedReveal([] as string[]));
    expect(result.current.revealed).toEqual([]);
    expect(result.current.done).toBe(true);
  });

  it("restarts when the items array identity changes", () => {
    const { result, rerender } = renderHook<
      { revealed: string[]; revealedCount: number; done: boolean },
      { items: string[] }
    >(({ items }) => useStagedReveal(items, { delayMs: 100 }), {
      initialProps: { items: ["x"] },
    });

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.done).toBe(true);

    rerender({ items: ["y", "z"] });
    expect(result.current.revealed).toEqual([]);
    expect(result.current.done).toBe(false);

    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current.revealed).toEqual(["y", "z"]);
  });
});
