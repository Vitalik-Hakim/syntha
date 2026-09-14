import type { ReactNode } from "react";

import { tone } from "@/lib/format";

export function Section({ title, aside, children, className = "" }: {
  title: string; aside?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`border border-line bg-surface ${className}`}>
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
        <h2 className="text-xs font-medium uppercase tracking-wider text-ink-muted">{title}</h2>
        {aside}
      </div>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

/** Signed money/percent: sign character + weight + colour, so hue is never the only cue. */
export function Signed({ value, children, className = "" }: {
  value: string | number | null | undefined; children: ReactNode; className?: string;
}) {
  const t = tone(value);
  const cls = t === "gain" ? "text-gain font-semibold" : t === "loss" ? "text-loss font-semibold" : "text-ink";
  return <span className={`num ${cls} ${className}`}>{children}</span>;
}

export function Stat({ label, children, sub, muted = false, title }: {
  label: string; children: ReactNode; sub?: ReactNode; muted?: boolean; title?: string;
}) {
  return (
    <div className={`border border-line bg-surface px-4 py-3 ${muted ? "opacity-60" : ""}`} title={title}>
      <div className="text-xs uppercase tracking-wider text-ink-faint">{label}</div>
      <div className="mt-1 text-xl">{children}</div>
      {sub ? <div className="mt-1 text-xs text-ink-muted">{sub}</div> : null}
    </div>
  );
}

const BADGE_TONES: Record<string, string> = {
  DONE: "border-line text-ink-muted",
  RUNNING: "border-accent text-accent",
  QUEUED: "border-line text-ink-muted",
  FAILED: "border-loss text-loss",
  PARTIAL: "border-dashed border-warn text-warn",
  COMPLETE: "border-line text-ink-muted",
  NO_SIGNALS: "border-dashed border-line text-ink-faint",
  FILLED: "border-line text-ink-muted",
  UNEXECUTABLE: "border-dashed border-warn text-warn",
  REJECTED_MIN_SIZE: "border-dashed border-loss text-loss",
};

export function Badge({ children, kind }: { children: ReactNode; kind?: string }) {
  const cls = (kind && BADGE_TONES[kind]) || "border-line text-ink-muted";
  return (
    <span className={`inline-block whitespace-nowrap border px-1.5 py-0.5 font-mono text-[11px] uppercase tracking-wide ${cls}`}>
      {children}
    </span>
  );
}

export const DISCLAIMER_TITLE = "SIMULATED RESULTS — NOT ACTUAL TRADING PERFORMANCE.";
export const DISCLAIMER_BODY =
  "Backtested returns are hypothetical, rely on the execution assumptions shown below, and do not account for factors that affect real trading. Past performance does not indicate future results. This tool is for research and is not financial advice.";

/** Required on every results view (PRD §19). */
export function Disclaimer() {
  return (
    <div role="note" className="mb-5 border border-line bg-raised px-4 py-2 text-xs leading-relaxed text-ink-muted">
      <strong className="font-semibold text-ink">{DISCLAIMER_TITLE}</strong> {DISCLAIMER_BODY}
    </div>
  );
}

export function Warning({ children }: { children: ReactNode }) {
  return <div className="border-l-2 border-warn bg-raised px-3 py-2 text-sm text-ink">{children}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="py-8 text-sm text-ink-faint">{label}</p>;
}

export function ErrorState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="border border-loss/60 px-4 py-3">
      <p className="text-sm font-semibold text-loss">{title}</p>
      {detail ? <pre className="mt-2 whitespace-pre-wrap font-mono text-xs text-ink-muted">{detail}</pre> : null}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="border border-dashed border-line px-6 py-8">
      <p className="text-sm text-ink">{title}</p>
      {children ? <div className="mt-2 max-w-prose text-sm text-ink-muted">{children}</div> : null}
    </div>
  );
}
