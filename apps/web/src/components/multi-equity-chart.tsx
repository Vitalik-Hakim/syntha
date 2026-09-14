"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { Comparison } from "@/lib/api/client";

export const SERIES_COLORS = ["#d9a54a", "#7fb3d5", "#c39bd3", "#76d7c4", "#f0b27a", "#aeb6bf"];
const DASHES = [[], [6, 4], [2, 3], [10, 3, 2, 3], [4, 4], [1, 2]];

/** Equity curves of compared runs on one time axis (server-downsampled, stepped). */
export function MultiEquityChart({ comparison }: { comparison: Comparison }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const lines = comparison.equity;
    const x = [...new Set(lines.flatMap((l) => l.ts))].sort((a, b) => a - b);
    const data: uPlot.AlignedData = [
      x,
      ...lines.map((l) => {
        const byTs = new Map(l.ts.map((t, i) => [t, l.equity[i]!]));
        return x.map((t) => byTs.get(t) ?? null);
      }),
    ];
    const u = new uPlot(
      {
        width: el.clientWidth,
        height: 300,
        legend: { show: true },
        scales: { x: { time: true } },
        axes: [
          { stroke: "#6b6f77", grid: { stroke: "#2a2d32", width: 1 }, font: "11px ui-monospace, Menlo, monospace" },
          { stroke: "#6b6f77", grid: { stroke: "#2a2d32", width: 1 }, font: "11px ui-monospace, Menlo, monospace", values: (_u, v) => v.map((n) => (n == null ? "" : `$${n.toFixed(0)}`)) },
        ],
        series: [
          {},
          ...lines.map((l, i) => ({
            label: l.run_id,
            stroke: SERIES_COLORS[i % SERIES_COLORS.length],
            dash: DASHES[i % DASHES.length],
            width: 1.5,
            spanGaps: true,
            paths: uPlot.paths.stepped?.({ align: 1 }),
          })),
        ],
      },
      data,
      el,
    );
    const onResize = () => u.setSize({ width: el.clientWidth, height: 300 });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      u.destroy();
    };
  }, [comparison]);
  return <div ref={host} className="w-full" />;
}
