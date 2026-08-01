// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTypewriter } from "@/hooks/use-typewriter";

describe("useTypewriter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts with nothing revealed when enabled", () => {
    const { result } = renderHook(() => useTypewriter("hello world", true));
    expect(result.current.text).toBe("");
    expect(result.current.done).toBe(false);
  });

  it("reveals the full text and reports done once enough time passes", () => {
    const { result } = renderHook(() => useTypewriter("hello world", true));

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(result.current.text).toBe("hello world");
    expect(result.current.done).toBe(true);
  });

  it("reveals text immediately, with no animation, when disabled", () => {
    const { result } = renderHook(() => useTypewriter("hello world", false));
    expect(result.current.text).toBe("hello world");
    expect(result.current.done).toBe(true);
  });

  it("treats an empty string as immediately done", () => {
    const { result } = renderHook(() => useTypewriter("", true));
    expect(result.current.text).toBe("");
    expect(result.current.done).toBe(true);
  });

  it("restarts the reveal from scratch when fullText changes", () => {
    const { result, rerender } = renderHook(({ text }) => useTypewriter(text, true), {
      initialProps: { text: "first message" },
    });

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.done).toBe(true);

    rerender({ text: "second, different message" });
    expect(result.current.done).toBe(false);

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.text).toBe("second, different message");
    expect(result.current.done).toBe(true);
  });
});
