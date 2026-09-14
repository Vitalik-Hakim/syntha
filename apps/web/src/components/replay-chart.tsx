"use client";

import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

import type { Replay } from "@/lib/api/client";

const C = {
  price: "#e6e7e9",
  grid: "#2a2d32",
  axis: "#6b6f77",
  accent: "#d9a54a",
  gain: "#5fb88a",
  loss: "#e07a6b",
  muted: "#9a9ea6",
};

const FONT = "11px ui-monospace, Menlo, monospace";

/** Marker glyphs: shape carries the meaning, colour only reinforces it. */
export const MARKER_STYLE: Record<string, { color: string; glyph: string; name: string }> = {
  SIGNAL: { color: C.muted, glyph: "│", name: "Signal bar" },
  ENTRY: { color: C.accent, glyph: "▲", name: "Entry fill" },
  ATTEMPT: { color: C.accent, glyph: "✕", name: "Refused attempt" },
  EXIT_SIGNAL: { color: C.muted, glyph: "┆", name: "Exit signal" },
  EXIT_FILL: { color: C.accent, glyph: "▼", name: "Exit fill" },
  SETTLE: { color: C.accent, glyph: "■", name: "Settlement" },
  END_OF_BACKTEST: { color: C.muted, glyph: "□", name: "End of backtest" },
  RESOLVED: { color: C.muted, glyph: "┃", name: "Market resolved" },
  NEAR_MISS: { color: C.accent, glyph: "◇", name: "Near miss" },
};

const VERTICAL = new Set(["SIGNAL", "EXIT_SIGNAL", "RESOLVED", "NEAR_MISS"]);

/**
 * PRD §13.3: price ±2 h around the decision, trade prints as dots sized by volume (filled = taker
 * buy, hollow = taker sell), entry/exit markers, shaded lookback windows, exit levels, resolution.
 * Every coordinate comes from the replay endpoint; nothing is derived here beyond pixel geometry.
 */
export function ReplayChart({ replay, height = 340 }: { replay: Replay; height?: number }) {
  const host = useRef<HTMLDivElement>(null);
  const tip = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const t0 = new Date(replay.start).getTime() / 1000;
    const t1 = new Date(replay.end).getTime() / 1000;
    const x = replay.prices.ts;
    const y = replay.prices.price;
    const prints = replay.prints;
    const markers = replay.markers.map((m) => ({ ...m, t: new Date(m.ts).getTime() / 1000 }));
    const windows = replay.windows.map((w) => ({
      ...w,
      s: new Date(w.start).getTime() / 1000,
      e: new Date(w.end).getTime() / 1000,
    }));

    // Scale to what traded; a level far from the action would flatten it (it is listed instead).
    const values = [...y, ...prints.price, ...markers.flatMap((m) => (m.price == null ? [] : [m.price]))];
    const dataLo = values.length ? Math.min(...values) : 0;
    const dataHi = values.length ? Math.max(...values) : 1;
    const near = Math.max(dataHi - dataLo, 0.01) * 1.5;
    const levels = replay.levels.filter((l) => l.price >= dataLo - near && l.price <= dataHi + near);
    const lo = Math.min(dataLo, ...levels.map((l) => l.price));
    const hi = Math.max(dataHi, ...levels.map((l) => l.price));
    const pad = Math.max((hi - lo) * 0.12, 0.002);
    const yRange: [number, number] = [Math.max(0, lo - pad), Math.min(1, hi + pad)];
    const maxSize = prints.size.length ? Math.max(...prints.size) : 1;

    // Points before the window keep the stepped line honest at the left edge.
    const data: uPlot.AlignedData = [x.length ? x : [t0, t1], x.length ? y : [null, null]];

    const drawWindows = (u: uPlot) => {
      const ctx = u.ctx;
      const top = u.bbox.top;
      const h = u.bbox.height;
      ctx.save();
      windows.forEach((w, i) => {
        const a = u.valToPos(Math.max(w.s, t0), "x", true);
        const b = u.valToPos(Math.min(w.e, t1), "x", true);
        ctx.fillStyle = w.side === "exit" ? "rgba(224,122,107,0.08)" : "rgba(217,165,74,0.08)";
        ctx.fillRect(a, top, Math.max(b - a, 1), h);
        const r = uPlot.pxRatio;
        const text = `${w.label} ${w.passed ? "✓" : w.passed === false ? "✗" : ""}`;
        ctx.font = `${10 * r}px ui-monospace, Menlo, monospace`;
        const ty = top + (12 + i * 13) * r;
        ctx.fillStyle = "rgba(23,25,28,0.85)";
        ctx.fillRect(b + 3 * r, ty - 10 * r, ctx.measureText(text).width + 6 * r, 13 * r);
        ctx.fillStyle = C.muted;
        ctx.fillText(text, b + 6 * r, ty);
      });
      ctx.restore();
    };

    const drawOverlay = (u: uPlot) => {
      const ctx = u.ctx;
      const r = uPlot.pxRatio;
      const { left, top, width, height: h } = u.bbox;
      ctx.save();
      ctx.beginPath();
      ctx.rect(left, top, width, h);
      ctx.clip();

      const placed: number[] = [];
      for (const level of levels) {
        const py = u.valToPos(level.price, "y", true);
        // nudge a label up until it clears the ones already drawn
        let ly = py - 5 * r;
        while (placed.some((p) => Math.abs(p - ly) < 12 * r)) ly -= 12 * r;
        placed.push(ly);
        ctx.strokeStyle = level.kind === "STOP_LOSS_PCT" ? C.loss : level.kind === "ENTRY_PRICE_RULE" ? C.muted : C.gain;
        ctx.setLineDash(level.kind === "ENTRY_PRICE_RULE" ? [2 * r, 3 * r] : [6 * r, 4 * r]);
        ctx.lineWidth = r;
        ctx.beginPath();
        ctx.moveTo(left, py);
        ctx.lineTo(left + width, py);
        ctx.stroke();
        ctx.setLineDash([]);
        const color = ctx.strokeStyle;
        ctx.font = `${10 * r}px ui-monospace, Menlo, monospace`;
        const label = level.label;
        const lw = ctx.measureText(label).width;
        const lx = level.kind === "ENTRY_PRICE_RULE" ? left + 4 * r : left + width - lw - 4 * r;
        ctx.fillStyle = "rgba(23,25,28,0.85)";
        ctx.fillRect(lx - 2 * r, ly - 10 * r, lw + 4 * r, 13 * r);
        ctx.fillStyle = color;
        ctx.fillText(label, lx, ly);
      }

      for (let i = 0; i < prints.ts.length; i++) {
        const px = u.valToPos(prints.ts[i]!, "x", true);
        const py = u.valToPos(prints.price[i]!, "y", true);
        const radius = (2 + 6 * Math.sqrt(prints.size[i]! / maxSize)) * r;
        const buy = prints.side[i] === "BUY";
        ctx.beginPath();
        ctx.arc(px, py, radius, 0, 2 * Math.PI);
        ctx.lineWidth = r;
        if (buy) {
          ctx.fillStyle = "rgba(95,184,138,0.45)";
          ctx.fill();
        }
        ctx.strokeStyle = buy ? C.gain : C.loss;
        ctx.stroke();
      }

      for (const m of markers) {
        const style = MARKER_STYLE[m.kind] ?? { color: C.muted, glyph: "•", name: m.kind };
        const px = u.valToPos(m.t, "x", true);
        if (VERTICAL.has(m.kind) || m.price == null) {
          ctx.strokeStyle = style.color;
          ctx.lineWidth = r;
          ctx.setLineDash(m.kind === "RESOLVED" ? [] : m.kind === "NEAR_MISS" ? [2 * r, 2 * r] : [4 * r, 3 * r]);
          ctx.beginPath();
          ctx.moveTo(px, top);
          ctx.lineTo(px, top + h);
          ctx.stroke();
          ctx.setLineDash([]);
        }
        if (m.price != null) {
          const py = u.valToPos(m.price, "y", true);
          ctx.fillStyle = style.color;
          ctx.font = `${13 * r}px ui-sans-serif, system-ui`;
          const w = ctx.measureText(style.glyph).width;
          ctx.fillText(style.glyph, px - w / 2, py + 5 * r);
        }
      }
      ctx.restore();
    };

    const u = new uPlot(
      {
        width: el.clientWidth,
        height,
        legend: { show: false },
        cursor: { points: { show: false } },
        scales: { x: { time: true, range: [t0, t1] }, y: { range: yRange } },
        axes: [
          { stroke: C.axis, grid: { stroke: C.grid, width: 1 }, font: FONT },
          {
            stroke: C.axis,
            grid: { stroke: C.grid, width: 1 },
            font: FONT,
            label: "price",
            labelFont: "11px ui-sans-serif, system-ui",
            values: (_u, vals) => vals.map((v) => (v == null ? "" : v.toFixed(3))),
          },
        ],
        series: [{}, { label: "Price", stroke: C.price, width: 1.25, paths: uPlot.paths.stepped?.({ align: 1 }) }],
        hooks: {
          drawClear: [drawWindows],
          draw: [drawOverlay],
          setCursor: [
            (uu) => {
              const box = tip.current;
              if (!box) return;
              const { left, top } = uu.cursor;
              if (left == null || top == null || left < 0) {
                box.hidden = true;
                return;
              }
              const t = uu.posToVal(left, "x");
              const near = (secs: number) => Math.abs(uu.valToPos(secs, "x") - left) < 6;
              const lines: string[] = [];
              for (const m of markers) if (near(m.t)) lines.push(`${MARKER_STYLE[m.kind]?.name ?? m.kind}: ${m.label}`);
              for (let i = 0; i < prints.ts.length; i++) {
                if (near(prints.ts[i]!) && Math.abs(uu.valToPos(prints.price[i]!, "y") - top) < 10) {
                  lines.push(`Print ${prints.side[i]} ${prints.size[i]!.toLocaleString("en-US", { maximumFractionDigits: 2 })} @ ${prints.price[i]!.toFixed(3)}`);
                }
              }
              const idx = uu.valToIdx(t);
              const p = y[idx];
              const stamp = new Date(t * 1000).toISOString().slice(11, 19);
              lines.unshift(`${stamp} UTC${p != null && x.length ? ` · price ${p.toFixed(3)}` : ""}`);
              box.textContent = lines.join("\n");
              box.hidden = false;
              // cursor coordinates are relative to the plot area; the tooltip is positioned in the wrapper
              const over = uu.over.getBoundingClientRect();
              const wrap = box.parentElement!.getBoundingClientRect();
              const x0 = over.left - wrap.left;
              box.style.left = `${Math.min(x0 + left + 12, wrap.width - 260)}px`;
              box.style.top = `${over.top - wrap.top + top + 12}px`;
            },
          ],
        },
      },
      data,
      el,
    );
    const onResize = () => u.setSize({ width: el.clientWidth, height });
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      u.destroy();
    };
  }, [replay, height]);

  return (
    <div className="relative">
      <div ref={host} className="w-full" />
      <div
        ref={tip}
        hidden
        className="pointer-events-none absolute z-10 max-w-[18rem] whitespace-pre border border-line bg-raised px-2 py-1 font-mono text-[11px] text-ink"
      />
      <ReplayLegend replay={replay} />
    </div>
  );
}

function ReplayLegend({ replay }: { replay: Replay }) {
  const kinds = [...new Set(replay.markers.map((m) => m.kind))];
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted">
      {kinds.map((k) => (
        <span key={k}>
          <span style={{ color: MARKER_STYLE[k]?.color }}>{MARKER_STYLE[k]?.glyph ?? "•"}</span> {MARKER_STYLE[k]?.name ?? k}
        </span>
      ))}
      {replay.prints.ts.length ? (
        <span>
          <span className="text-gain">●</span> taker buy · <span className="text-loss">○</span> taker sell, sized by shares
        </span>
      ) : (
        <span>no trade prints in this window</span>
      )}
      {replay.windows.length ? <span><span className="text-accent">▮</span> lookback window read at the decision bar</span> : null}
      {replay.levels.length ? <span>- - exit levels · ··· entry price rule</span> : null}
      {offChart(replay).map((l) => (
        <span key={l.label} className="text-ink-faint">{l.label} (off chart)</span>
      ))}
      <span className="text-ink-faint">
        {replay.prices.downsampled
          ? `${replay.prices.price.length.toLocaleString()} of ${replay.prices.total_points.toLocaleString()} price samples (LTTB)`
          : `${replay.prices.total_points.toLocaleString()} price samples (mid-like, not trades)`}
        {replay.prints.truncated ? " · prints truncated" : ""}
      </span>
    </div>
  );
}

function offChart(replay: Replay) {
  const values = [...replay.prices.price, ...replay.prints.price, ...replay.markers.flatMap((m) => (m.price == null ? [] : [m.price]))];
  if (!values.length) return [];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const near = Math.max(hi - lo, 0.01) * 1.5;
  return replay.levels.filter((l) => l.price < lo - near || l.price > hi + near);
}
