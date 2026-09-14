"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { EquitySeries } from "@/lib/api/client";

const COLORS = { equity: "#d9a54a", drawdown: "#e07a6b", grid: "#2a2d32", axis: "#6b6f77", benchmark: "#9a9ea6" };

function axis(label: string, fmt: (v: number) => string): uPlot.Axis {
  return {
    label,
    stroke: COLORS.axis,
    grid: { stroke: COLORS.grid, width: 1 },
    ticks: { stroke: COLORS.grid, width: 1 },
    font: "11px ui-monospace, Menlo, monospace",
    labelFont: "11px ui-sans-serif, system-ui",
    values: (_u, vals) => vals.map((v) => (v === null ? "" : fmt(v))),
  };
}

/**
 * Equity (top) and drawdown (bottom) on a shared time axis. Both series come from the API:
 * drawdown is computed server-side on the full-resolution curve (PRD §0 rule 3).
 * The curve is a step function (equity only changes at stored change points).
 */
export function EquityChart({ series, benchmark }: { series: EquitySeries; benchmark?: [string, number][] | null }) {
  const top = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!top.current || !bottom.current || series.points.length === 0) return;
    // Buy-and-hold (engine-computed, last equity of each UTC day) shares the time axis: both series
    // are laid on the union of timestamps and span the gaps.
    const own = new Map(series.points.map((p) => [new Date(p.ts).getTime() / 1000, p]));
    const bench = new Map((benchmark ?? []).map(([day, value]) => [new Date(`${day}T23:59:00Z`).getTime() / 1000, value]));
    const x = [...new Set([...own.keys(), ...bench.keys()])].sort((a, b) => a - b);
    const equity = x.map((t) => (own.has(t) ? Number(own.get(t)!.equity) : null));
    const benchmarkValues = x.map((t) => bench.get(t) ?? null);
    const drawdown = x.map((t) => (own.has(t) ? -own.get(t)!.drawdown_pct * 100 : null));
    const width = top.current.clientWidth;
    const sync = uPlot.sync("equity");
    const cursor = { sync: { key: sync.key } };
    const step = uPlot.paths.stepped?.({ align: 1 });

    const eq = new uPlot(
      {
        width,
        height: 260,
        cursor,
        legend: { show: false },
        scales: { x: { time: true } },
        axes: [
          { stroke: COLORS.axis, grid: { stroke: COLORS.grid, width: 1 }, font: "11px ui-monospace, Menlo, monospace" },
          axis("equity", (v) => `$${v.toFixed(0)}`),
        ],
        series: [
          {},
          { label: "Equity", stroke: COLORS.equity, width: 1.5, paths: step, spanGaps: true },
          { label: "Buy and hold", stroke: COLORS.benchmark, width: 1, dash: [4, 4], spanGaps: true, show: bench.size > 0 },
        ],
      },
      [x, equity, benchmarkValues],
      top.current,
    );
    const dd = new uPlot(
      {
        width,
        height: 120,
        cursor,
        legend: { show: false },
        scales: { x: { time: true } },
        axes: [
          { stroke: COLORS.axis, grid: { stroke: COLORS.grid, width: 1 }, font: "11px ui-monospace, Menlo, monospace" },
          axis("drawdown", (v) => `${v.toFixed(0)}%`),
        ],
        series: [{}, { label: "Drawdown", stroke: COLORS.drawdown, fill: "rgba(224,122,107,0.15)", width: 1, paths: step, spanGaps: true }],
      },
      [x, drawdown],
      bottom.current,
    );
    const onResize = () => {
      const w = top.current?.clientWidth ?? width;
      eq.setSize({ width: w, height: 260 });
      dd.setSize({ width: w, height: 120 });
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      eq.destroy();
      dd.destroy();
    };
  }, [series, benchmark]);

  return (
    <div>
      <div ref={top} className="w-full" />
      <div ref={bottom} className="w-full" />
      <p className="mt-1 text-[11px] text-ink-faint">
        {benchmark?.length ? <span className="mr-2"><span className="text-ink-muted">- - -</span> buy and hold on the same markets (daily).</span> : null}
        {series.downsampled
          ? `${series.returned_points.toLocaleString()} of ${series.total_points.toLocaleString()} change points shown (LTTB, server-side). Drawdown computed on the full curve.`
          : `${series.total_points.toLocaleString()} change points. Equity is marked to market every bar, including unrealized P&L.`}
      </p>
    </div>
  );
}
