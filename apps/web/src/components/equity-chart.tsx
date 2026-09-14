"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { EquitySeries } from "@/lib/api/client";

const COLORS = { equity: "#d9a54a", drawdown: "#e07a6b", grid: "#2a2d32", axis: "#6b6f77" };

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
export function EquityChart({ series }: { series: EquitySeries }) {
  const top = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!top.current || !bottom.current || series.points.length === 0) return;
    const x = series.points.map((p) => new Date(p.ts).getTime() / 1000);
    const equity = series.points.map((p) => Number(p.equity));
    const drawdown = series.points.map((p) => -p.drawdown_pct * 100);
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
        series: [{}, { label: "Equity", stroke: COLORS.equity, width: 1.5, paths: step }],
      },
      [x, equity],
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
        series: [{}, { label: "Drawdown", stroke: COLORS.drawdown, fill: "rgba(224,122,107,0.15)", width: 1, paths: step }],
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
  }, [series]);

  return (
    <div>
      <div ref={top} className="w-full" />
      <div ref={bottom} className="w-full" />
      <p className="mt-1 text-[11px] text-ink-faint">
        {series.downsampled
          ? `${series.returned_points.toLocaleString()} of ${series.total_points.toLocaleString()} change points shown (LTTB, server-side). Drawdown computed on the full curve.`
          : `${series.total_points.toLocaleString()} change points. Equity is marked to market every bar, including unrealized P&L.`}
      </p>
    </div>
  );
}
