/**
 * Rule checklists rendered from stored traces (PRD §13.1). Every row is a check the engine ran,
 * with the value it saw; nothing here is re-evaluated in the browser.
 */
import type { ReactNode } from "react";

import { Badge } from "@/components/ui";
import { duration, pct, shares, ts, usd } from "@/lib/format";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Trace = Record<string, any>;

const OPS: Record<string, string> = { lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", neq: "≠" };

const FILTER_LABELS: Record<string, string> = {
  market_open: "Market open",
  min_volume_asof: "Traded notional to date",
  min_time_to_resolution_minutes: "Time to scheduled end",
  max_stale_minutes: "Price staleness",
};

export const BLOCKER_LABELS: Record<string, string> = {
  before_market_open: "Before market open",
  below_min_volume_asof: "Below min volume (as of the bar)",
  below_min_time_to_resolution: "Too close to scheduled end",
  stale_price: "Price older than the staleness limit",
  max_open_positions: "Max open positions reached",
  max_positions_per_event: "Already a position in this event",
  reentry_not_allowed: "Re-entry not allowed",
  cooldown: "Market cooldown",
};

export function featureName(c: Trace): string {
  const params = c.params ? Object.entries(c.params).map(([k, v]) => `${k.replace("_minutes", "")}=${v}m`) : [];
  return params.length ? `${c.feature}(${params.join(", ")})` : String(c.feature);
}

/** Display a feature value in its unit. */
export function featureValue(feature: string, value: unknown): string {
  if (value === null || value === undefined) return "no data";
  if (typeof value === "string") return value.includes("T") ? ts(value) : value;
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (feature === "price" || feature === "vwap_gap" || feature === "spread_proxy" || feature === "random_draw") return n.toFixed(4);
  if (feature.endsWith("_pct")) return pct(n, { signed: true, digits: 2 });
  if (feature === "price_change_abs") return `${n.toFixed(2)}¢`;
  if (feature.includes("minutes") || feature === "min_time_to_resolution_minutes") return duration(n * 60);
  if (feature === "min_volume_asof") return usd(n, { digits: Math.abs(n) < 100 ? 2 : 0 });
  if (feature === "realized_vol" || feature === "volume_ratio") return n.toFixed(3);
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

type State = "pass" | "fail" | "null";

function Glyph({ state }: { state: State }) {
  const cls = state === "pass" ? "text-gain" : state === "fail" ? "text-loss" : "text-warn";
  const glyph = state === "pass" ? "✓" : state === "fail" ? "✗" : "∅";
  const text = state === "pass" ? "passed" : state === "fail" ? "failed" : "no data";
  return (
    <span className={`w-4 shrink-0 text-center font-semibold ${cls}`} title={text}>
      <span aria-hidden>{glyph}</span>
      <span className="sr-only">{text}</span>
    </span>
  );
}

function CheckRow({ state, name, rule, actual, note, emphasis = false }: {
  state: State; name: ReactNode; rule: ReactNode; actual: ReactNode; note?: ReactNode; emphasis?: boolean;
}) {
  return (
    <li className={`grid grid-cols-[1rem_minmax(0,1fr)_auto] items-baseline gap-x-2 py-1 ${emphasis ? "bg-raised -mx-2 px-2" : ""}`}>
      <Glyph state={state} />
      <div className="min-w-0">
        <span className="font-mono text-ink">{name}</span> <span className="text-ink-muted">{rule}</span>
        {note ? <div className="text-[11px] text-ink-faint">{note}</div> : null}
      </div>
      <span className={`num text-right ${state === "fail" ? "text-loss" : state === "null" ? "text-warn" : "text-ink"}`}>{actual}</span>
    </li>
  );
}

function stateOf(c: Trace): State {
  if (c.null || (c.actual === null && !c.passed)) return "null";
  return c.passed ? "pass" : "fail";
}

function windowNote(c: Trace): ReactNode {
  if (!c.window) return null;
  return <>reads {ts(c.window.from).slice(11)} → {ts(c.window.to).slice(11)} UTC</>;
}

export function ClauseList({ clauses }: { clauses: Trace[] }) {
  return (
    <ul className="text-xs">
      {clauses.map((c, i) => (
        <CheckRow
          key={i}
          state={stateOf(c)}
          name={featureName(c)}
          rule={`${OPS[c.op] ?? c.op} ${featureValue(c.feature, c.threshold)}`}
          actual={featureValue(c.feature, c.actual)}
          note={windowNote(c)}
        />
      ))}
    </ul>
  );
}

export function UniverseList({ checks }: { checks: Trace[] }) {
  return (
    <ul className="text-xs">
      {checks.map((c) => (
        <CheckRow
          key={c.filter}
          state={c.actual === null ? "null" : c.passed ? "pass" : "fail"}
          name={FILTER_LABELS[c.filter] ?? c.filter}
          rule={`${OPS[c.op] ?? c.op} ${featureValue(c.filter, c.threshold)}`}
          actual={featureValue(c.filter, c.actual)}
        />
      ))}
    </ul>
  );
}

/** Why the strategy entered, or why it didn't: universe filters, then the entry rules. */
export function EntryChecklist({ trace, mode }: { trace: Trace; mode: "all" | "any" | null }) {
  const clauses: Trace[] = trace.clauses ?? [];
  const universe: Trace[] | undefined = trace.universe;
  return (
    <div className="space-y-3">
      <div>
        <h4 className="mb-1 text-[11px] uppercase tracking-wider text-ink-faint">Universe filters</h4>
        {universe ? (
          universe.length ? <UniverseList checks={universe} /> : <p className="text-xs text-ink-faint">No filters configured.</p>
        ) : (
          <p className="text-xs text-ink-faint">Not recorded by this run (engine before M4).</p>
        )}
      </div>
      <div>
        <h4 className="mb-1 text-[11px] uppercase tracking-wider text-ink-faint">
          Entry rules {mode ? <span className="normal-case tracking-normal">— {mode === "all" ? "all must pass" : "any one must pass"}</span> : null}
        </h4>
        {clauses.length ? <ClauseList clauses={clauses} /> : <p className="text-xs text-ink-faint">No clauses (random or custom entry).</p>}
      </div>
    </div>
  );
}

function exitRule(c: Trace): { name: string; rule: string; actual: string } {
  switch (c.type) {
    case "TAKE_PROFIT_PCT":
      return { name: "Take profit", rule: `price ≥ ${Number(c.target).toFixed(4)} (entry +${pct(c.value, { digits: 0 })})`, actual: featureValue("price", c.price) };
    case "STOP_LOSS_PCT":
      return { name: "Stop loss", rule: `price ≤ ${Number(c.target).toFixed(4)} (entry −${pct(c.value, { digits: 0 })})`, actual: featureValue("price", c.price) };
    case "PRICE_ABOVE":
      return { name: "Price above", rule: `price ≥ ${Number(c.target).toFixed(4)}`, actual: featureValue("price", c.price) };
    case "PRICE_BELOW":
      return { name: "Price below", rule: `price ≤ ${Number(c.target).toFixed(4)}`, actual: featureValue("price", c.price) };
    case "MAX_HOLD_MINUTES":
      return { name: "Max hold", rule: `held ≥ ${duration(c.limit * 60)}`, actual: duration(c.holding_minutes * 60) };
    default:
      return c.clause
        ? { name: featureName(c.clause), rule: `${OPS[c.clause.op] ?? c.clause.op} ${featureValue(c.clause.feature, c.clause.threshold)}`, actual: featureValue(c.clause.feature, c.clause.actual) }
        : { name: String(c.type), rule: "", actual: "" };
  }
}

/** Why the position closed: every exit rule at the bar that fired, or the settlement record. */
export function ExitChecklist({ trace, status }: { trace: Trace | null; status: string }) {
  if (status !== "FILLED" && status !== "PARTIAL") {
    return <p className="text-xs text-ink-faint">No position was opened, so there was nothing to exit.</p>;
  }
  if (!trace || !trace.decision) return <p className="text-xs text-ink-faint">No exit recorded.</p>;
  if (trace.decision === "SETTLE") {
    return (
      <div className="space-y-1 text-xs">
        <p className="text-ink">Held to resolution: <span className="font-mono">{trace.resolution}</span>, paying <span className="num">{usd(trace.payout, { digits: 2 })}</span> per share.</p>
        {trace.settled_on_stale_data ? <p className="text-warn">Settled on stale data: no price sample near the resolution time.</p> : null}
        <p className="text-ink-faint">No exit rule fired before the market resolved.</p>
      </div>
    );
  }
  if (trace.decision === "END_OF_BACKTEST") {
    return (
      <p className="text-xs text-ink">
        Still open when the backtest ended: valued at its last mark <span className="num">{trace.valued_at_mark}</span>
        {trace.mark_staleness_minutes != null ? <> ({duration(trace.mark_staleness_minutes * 60)} old)</> : null}, with no exit costs.
      </p>
    );
  }
  const checks: Trace[] = trace.checks ?? (trace.rule ? [{ ...trace.rule, passed: true, fired: true }] : []);
  return (
    <div className="space-y-2">
      <p className="text-xs text-ink-muted">
        Evaluated at <span className="num text-ink">{ts(trace.ts)}</span>
        {trace.price != null ? <> at price <span className="num text-ink">{Number(trace.price).toFixed(4)}</span></> : null}; rules checked in order, the first that holds names the exit.
      </p>
      <ul className="text-xs">
        {checks.map((c, i) => {
          const r = exitRule(c);
          const state: State = c.clause && c.clause.null ? "null" : c.passed ? "pass" : "fail";
          return (
            <CheckRow
              key={i}
              state={state}
              name={<>{r.name} {c.fired ? <Badge>fired</Badge> : null}</>}
              rule={r.rule}
              actual={r.actual}
              note={c.clause ? windowNote(c.clause) : null}
              emphasis={Boolean(c.fired)}
            />
          );
        })}
      </ul>
    </div>
  );
}

const SIZING_LABELS: [string, string, (v: any) => string][] = [
  ["mode", "Sizing mode", String],
  ["equity", "Equity at entry", (v) => usd(v)],
  ["requested_notional", "Requested notional", (v) => usd(v)],
  ["final_notional", "Final notional", (v) => usd(v)],
  ["size_binding_constraint", "Binding constraint", (v) => (v == null ? "none" : String(v))],
];

const EXECUTION_LABELS: [string, string, (v: any) => string][] = [
  ["model", "Execution model", String],
  ["status", "Status", String],
  ["reason", "Refusal", (v) => (v == null ? "—" : String(v))],
  ["fallback", "Fallback", String],
  ["ref_price", "Reference price", (v) => Number(v).toFixed(4)],
  ["vwap", "Bar VWAP", (v) => Number(v).toFixed(4)],
  ["slippage_ticks", "Slippage (ticks)", String],
  ["fill_price", "Fill price", (v) => Number(v).toFixed(4)],
  ["bar_print_volume", "Printed shares in bar", (v) => shares(v)],
  ["cap", "Liquidity cap (shares)", (v) => shares(v)],
  ["shares", "Shares", (v) => shares(v)],
  ["min_order_size", "Market minimum", (v) => shares(v)],
  ["fee_schedule", "Fee schedule", (v) => (v == null ? "none" : String(v))],
  ["fee_rate", "Fee rate", (v) => String(v)],
  ["fee_exponent", "Fee exponent", String],
  ["fee", "Fee", (v) => usd(v, { digits: 5 })],
];

export function KeyValues({ data, labels }: { data: Trace | null | undefined; labels: [string, string, (v: any) => string][] }) {
  if (!data) return <p className="text-xs text-ink-faint">Not reached: the attempt was refused before this step.</p>;
  const known = new Set(labels.map(([k]) => k));
  const rows = [
    ...labels.filter(([k]) => data[k] !== undefined).map(([k, label, fmt]) => [label, fmt(data[k])] as const),
    ...Object.entries(data)
      .filter(([k]) => !known.has(k))
      .map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)] as const),
  ];
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-ink-muted">{label}</dt>
          <dd className="num text-right text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SizingTable({ sizing }: { sizing: Trace | null | undefined }) {
  return <KeyValues data={sizing} labels={SIZING_LABELS} />;
}

export function ExecutionTable({ execution }: { execution: Trace | null | undefined }) {
  return <KeyValues data={execution} labels={EXECUTION_LABELS} />;
}
