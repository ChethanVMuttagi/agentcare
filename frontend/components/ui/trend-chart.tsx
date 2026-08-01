"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

export interface TrendPoint {
  label: string;
  value: number;
}

const WIDTH = 480;
const HEIGHT = 120;
const PADDING_X = 8;
const PADDING_Y = 12;

/** A minimal, dependency-free SVG line/area chart for a short time
 * series — the sibling of `components/ui/bar-chart.tsx` for trend data.
 * Single series only, so no legend (the card title already names it —
 * a legend only earns its place at 2+ series). */
export function TrendChart({
  data,
  tone = "info",
}: {
  data: TrendPoint[];
  tone?: "info" | "success";
}) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="text-sm text-slate-400">No data yet.</p>;
  }

  const max = Math.max(1, ...data.map((point) => point.value));
  const stepX = data.length > 1 ? (WIDTH - PADDING_X * 2) / (data.length - 1) : 0;
  const colorClass = tone === "success" ? "text-emerald-500" : "text-blue-500";

  const xFor = (index: number) => PADDING_X + index * stepX;
  const yFor = (value: number) => HEIGHT - PADDING_Y - (value / max) * (HEIGHT - PADDING_Y * 2);

  const linePath = data
    .map((point, index) => `${index === 0 ? "M" : "L"}${xFor(index)},${yFor(point.value)}`)
    .join(" ");
  const areaPath = `${linePath} L${xFor(data.length - 1)},${HEIGHT - PADDING_Y} L${xFor(0)},${HEIGHT - PADDING_Y} Z`;
  const hovered = hoverIndex !== null ? data[hoverIndex] : null;

  return (
    <div className="relative">
      {hovered ? (
        <div className="pointer-events-none absolute top-0 right-0 rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-medium text-white dark:bg-slate-100 dark:text-slate-900">
          {hovered.label} · {hovered.value}
        </div>
      ) : null}
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        className={cn("h-28 w-full", colorClass)}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <path d={areaPath} fill="currentColor" opacity={0.12} stroke="none" />
        <path
          d={linePath}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {data.map((point, index) => (
          <g key={point.label}>
            {hoverIndex === index ? (
              <circle cx={xFor(index)} cy={yFor(point.value)} r={4} fill="currentColor" />
            ) : null}
            <rect
              x={xFor(index) - (stepX || WIDTH) / 2}
              y={0}
              width={stepX || WIDTH}
              height={HEIGHT}
              fill="transparent"
              onMouseEnter={() => setHoverIndex(index)}
            >
              <title>{`${point.label}: ${point.value}`}</title>
            </rect>
          </g>
        ))}
      </svg>
      <div className="flex justify-between text-[10px] text-slate-400">
        <span>{data[0]?.label}</span>
        <span>{data[data.length - 1]?.label}</span>
      </div>
    </div>
  );
}
