"use client";

import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode, WheelEvent as ReactWheelEvent } from "react";

import { cn } from "@/lib/utils";

const MIN_SCALE = 0.5;
const MAX_SCALE = 2.5;
const SCALE_STEP = 0.2;

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
}

/** A small, dependency-free pan/zoom viewport — drag to pan, wheel to
 * zoom, plus +/-/reset controls. Used by the Workflow Graph instead of a
 * library like react-flow, matching this codebase's existing
 * dependency-light convention for visualizations (see
 * `components/ui/bar-chart.tsx`). */
export function PanZoom({ children, className }: { children: ReactNode; className?: string }) {
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });
  const dragState = useRef<DragState | null>(null);

  function clampScale(scale: number): number {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const delta = event.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
    setTransform((previous) => ({ ...previous, scale: clampScale(previous.scale + delta) }));
  }

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragState.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: transform.x,
      originY: transform.y,
    };
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragState.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setTransform((previous) => ({
      ...previous,
      x: drag.originX + (event.clientX - drag.startX),
      y: drag.originY + (event.clientY - drag.startY),
    }));
  }

  function handlePointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (dragState.current?.pointerId === event.pointerId) dragState.current = null;
  }

  function zoomBy(delta: number) {
    setTransform((previous) => ({ ...previous, scale: clampScale(previous.scale + delta) }));
  }

  function reset() {
    setTransform({ scale: 1, x: 0, y: 0 });
  }

  return (
    <div className={cn("relative overflow-hidden", className)}>
      <div
        onWheel={handleWheel}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
        className="h-full w-full cursor-grab touch-none active:cursor-grabbing"
      >
        <div
          className="h-full w-full origin-top-left"
          style={{ transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})` }}
        >
          {children}
        </div>
      </div>
      <div className="absolute right-2 bottom-2 flex gap-1 rounded-md border border-slate-200 bg-white/90 p-1 shadow-sm backdrop-blur dark:border-slate-700 dark:bg-slate-900/90">
        <button
          type="button"
          onClick={() => zoomBy(-SCALE_STEP)}
          className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          aria-label="Zoom out"
        >
          <ZoomOut className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => zoomBy(SCALE_STEP)}
          className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          aria-label="Zoom in"
        >
          <ZoomIn className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={reset}
          className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
          aria-label="Reset view"
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
