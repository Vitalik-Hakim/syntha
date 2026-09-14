"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { EquityChart } from "@/components/equity-chart";
import { Badge, Disclaimer, EmptyState, ErrorState, Loading, Section, Signed, Stat, Warning } from "@/components/ui";
import { apiGet, type EquitySeries, type RunDetail } from "@/lib/api/client";
import { count, duration, pct, ts, usd } from "@/lib/format";

type Metrics = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type DQ = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

// Insertion order is display order: bars blocked at each gate, then what survived.
const FUNNEL_LABELS: Record<string, string> = {
  no_price_yet: "No price observed yet",
  trading_halted: "Market resolved or closed",
  before_market_open: "Before market open",
  below_min_volume_asof: "Below min volume (as of the bar)",
  below_min_time_to_resolution: "Too close to scheduled end",
  stale_price: "Price older than the staleness limit",
  eligible: "Eligible bars",
  entry_rules_true_when_eligible: "Entry rules true (eligible)",
  entry_rules_true_ignoring_universe: "Entry rules true (ignoring filters)",
};

function useRun(id: string) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRun(await apiGet<RunDetail>(`/backtests/${id}`));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = run?.status === "QUEUED" || run?.status === "RUNNING";
  useEffect(() => {
    if (!active) return;
    // Server-sent events for progress, with polling as a fallback if the stream is buffered.
    const source = new EventSource(`/api/backtests/${id}/stream`);
    source.addEventListener("progress", (e) => {
      const data = JSON.parse((e as MessageEvent).data) as { status: RunDetail["status"]; progress: RunDetail["progress"] };
      setRun((prev) => (prev ? { ...prev, status: data.status, progress: data.progress } : prev));
    });
    source.addEventListener("status", () => {
      source.close();
      void load();
    });
    const poll = window.setInterval(() => void load(), 2000);
    return () => {
      source.close();
      window.clearInterval(poll);
    };
  }, [active, id, load]);

  return { run, error };
}

export function RunView({ id }: { id: string }) {
  const { run, error } = useRun(id);
  const [equity, setEquity] = useState<EquitySeries | null>(null);

  useEffect(() => {
    if (run?.status !== "DONE") return;
    apiGet<EquitySeries>(`/backtests/${id}/equity?max_points=2000`).then(setEquity).catch(() => setEquity(null));
  }, [id, run?.status]);

  if (error && !run) return <ErrorState title={`Could not load ${id}`} detail={error} />;
  if (!run) return <Loading label="Loading run…" />;

  return (
    <div className="max-w-6xl">
      <Disclaimer />
      <RunHeader run={run} />
      {run.status === "QUEUED" || run.status === "RUNNING" ? <Progress run={run} /> : null}
      {run.status === "FAILED" ? (
        <ErrorState title="This run failed. No results were recorded." detail={run.error ?? undefined} />
      ) : null}
      {run.status === "DONE" && run.metrics && run.data_quality ? (
        <Results run={run} metrics={run.metrics} dq={run.data_quality} equity={equity} />
      ) : null}
    </div>
  );
}

function RunHeader({ run }: { run: RunDetail }) {
  const cfg = run.config;
  const dq = run.data_quality as DQ | null;
  const ex = cfg.execution;
  return (
    <header className="mb-5 border-b border-line pb-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-medium">{run.strategy_name}</h1>
        <Badge kind={run.status}>{run.status}</Badge>
        {dq?.execution_state ? <Badge kind={dq.execution_state}>{String(dq.execution_state).replace("_", " ")}</Badge> : null}
        <span className="num text-xs text-ink-faint">{run.id}</span>
        <span className="ml-auto flex gap-3 text-sm">
          {run.status === "DONE" ? (
            <>
              <Link className="text-accent hover:underline" href={`/runs/${run.id}/trades`}>Trade blotter →</Link>
              <a className="text-ink-muted hover:text-ink" href={`/api/backtests/${run.id}/export.csv`}>CSV</a>
              <a className="text-ink-muted hover:text-ink" href={`/api/backtests/${run.id}/export.json`}>JSON</a>
            </>
          ) : null}
        </span>
      </div>
      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-muted">
        <Meta label="collection" value={run.collection ?? "all markets"} />
        <Meta label="execution" value={`${ex?.model}${ex?.slippage_ticks != null ? ` · ${ex.slippage_ticks} tick slip` : ""}${ex?.liquidity_participation != null ? ` · ${pct(ex.liquidity_participation, { digits: 0 })} of prints` : " · unlimited size"}`} />
        <Meta label="fees" value={cfg.fees?.mode ?? "HISTORICAL"} />
        <Meta label="lag" value={`${cfg.decision_lag_bars} bar`} />
        <Meta label="bars" value={`${cfg.bar_interval_minutes}m`} />
        <Meta label="capital" value={usd(cfg.starting_capital as string)} />
        <Meta label="created" value={ts(run.created_at)} />
      </dl>
    </header>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-ink-faint">{label}</dt>
      <dd className="num text-ink">{value}</dd>
    </div>
  );
}

function Progress({ run }: { run: RunDetail }) {
  const p = run.progress;
  const phase = p?.phase ?? (run.status === "QUEUED" ? "queued" : "starting");
  const fraction = p?.fraction ?? 0;
  return (
    <Section title="Running">
      <p className="text-sm text-ink-muted">
        <span className="text-ink">{phase}</span>
        {p ? <span className="num"> · {pct(fraction, { digits: 0 })}</span> : null}
      </p>
      <div className="mt-2 h-1 w-full bg-raised">
        <div className="h-1 bg-accent transition-[width] duration-150" style={{ width: `${Math.round(fraction * 100)}%` }} />
      </div>
      <p className="mt-2 text-xs text-ink-faint">
        {run.status === "QUEUED" ? "Waiting for a worker. Is `make dev` (or `make dev-worker`) running?" : "Loading data, simulating every bar, then saving results."}
      </p>
    </Section>
  );
}

function Results({ run, metrics: m, dq, equity }: { run: RunDetail; metrics: Metrics; dq: DQ; equity: EquitySeries | null }) {
  const warnings: string[] = m.warnings ?? [];
  const lowSample = Boolean(m.low_sample);
  const costs = m.cost_attribution ?? {};
  return (
    <div className="space-y-5">
      {warnings.length ? <div className="space-y-2">{warnings.map((w) => <Warning key={w}>{w}</Warning>)}</div> : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Stat label="Net P&L" muted={lowSample} title={m.definitions?.net_profit}>
          <Signed value={m.net_profit}>{usd(m.net_profit, { signed: true })}</Signed>
        </Stat>
        <Stat label="ROI" muted={lowSample} title={m.definitions?.roi} sub={m.cagr == null ? m.cagr_note : `CAGR ${pct(m.cagr, { signed: true })}`}>
          <Signed value={m.roi}>{pct(m.roi, { signed: true })}</Signed>
        </Stat>
        <Stat label="Max drawdown" muted={lowSample} title={m.definitions?.max_drawdown} sub={`${usd(m.max_drawdown?.usd)} · ${duration(m.max_drawdown?.duration_seconds)}`}>
          <span className="num">{pct(m.max_drawdown?.pct)}</span>
        </Stat>
        <Stat label="Trades" muted={lowSample} title={m.definitions?.win_rate} sub={`win rate ${pct(m.win_rate, { digits: 1 })} · PF ${m.profit_factor == null ? "—" : typeof m.profit_factor === "string" ? "∞" : Number(m.profit_factor).toFixed(2)}`}>
          <span className="num">{count(m.n_trades)}</span>
        </Stat>
        <Stat
          label="Signals → filled"
          title="PRD §16: the most informative single ratio. Unfilled signals are recorded, not hidden."
          sub={`${count(dq.signals_unexecutable)} unexecutable · ${count(dq.signals_rejected_min_size)} below min size`}
        >
          <span className="num">{count(dq.signals_generated)} → {count(dq.signals_filled)}</span>
          <span className="num ml-2 text-sm text-ink-muted">{dq.signal_fill_rate == null ? "" : pct(dq.signal_fill_rate, { digits: 0 })}</span>
        </Stat>
        <Stat label="Sharpe (daily)" muted title={m.definitions?.sharpe} sub={m.sharpe == null ? m.sharpe_note : "indicative only"}>
          <span className="num">{m.sharpe == null ? "—" : Number(m.sharpe).toFixed(2)}</span>
        </Stat>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Section title="Equity">
          {lowSample ? <p className="mb-2 text-xs font-medium text-warn">Too few trades to distinguish this from luck.</p> : null}
          {equity ? <EquityChart series={equity} /> : <Loading label="Loading equity curve…" />}
        </Section>
        <Section title="Cost attribution" aside={<span className="text-[11px] text-ink-faint">never collapsed</span>}>
          <table className="num w-full text-sm">
            <tbody>
              <CostRow label="Gross P&L" value={costs.gross_pnl} signed />
              <CostRow label="Fees" value={-Math.abs(costs.fees ?? 0)} />
              <CostRow label="Slippage" value={-Math.abs(costs.slippage ?? 0)} />
              <tr className="border-t border-line">
                <td className="py-1.5 text-ink">Net P&L</td>
                <td className="py-1.5 text-right"><Signed value={costs.net_pnl}>{usd(costs.net_pnl, { signed: true })}</Signed></td>
              </tr>
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted" title={m.definitions?.cost_ratio}>
            Cost ratio <span className="num text-ink">{m.cost_ratio == null ? "—" : Number(m.cost_ratio).toFixed(2)}</span>
          </p>
          <dl className="mt-3 space-y-1 text-xs text-ink-muted">
            <Row label="Expectancy / trade" value={usd(m.expectancy, { signed: true })} />
            <Row label="Worst trade" value={m.worst_trade ? usd(m.worst_trade.net_pnl, { signed: true }) : "—"} />
            <Row label="Max consecutive losses" value={count(m.max_consecutive_losses)} />
            <Row label="Median holding" value={duration(m.holding_seconds_median)} />
          </dl>
        </Section>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Exit reasons">
          <Bars data={m.exit_reasons ?? {}} empty="No closed trades." />
        </Section>
        <Section title="Why signals didn't fill">
          <Bars data={dq.signals_unexecutable_reasons ?? {}} empty="Every signal filled." />
          {dq.signals_rejected_min_size ? (
            <p className="mt-2 text-xs text-ink-muted">
              Rejected for minimum order size: <span className="num text-ink">{count(dq.signals_rejected_min_size)}</span>
            </p>
          ) : null}
        </Section>
      </div>

      <DataQuality dq={dq} />
      <Manifest run={run} />
    </div>
  );
}

function CostRow({ label, value, signed = false }: { label: string; value: number | undefined; signed?: boolean }) {
  return (
    <tr>
      <td className="py-1 text-ink-muted">{label}</td>
      <td className="py-1 text-right"><Signed value={value}>{usd(value, { signed })}</Signed></td>
    </tr>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd className="num text-ink">{value}</dd>
    </div>
  );
}

function Bars({ data, empty }: { data: Record<string, number>; empty: string }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <p className="text-sm text-ink-faint">{empty}</p>;
  const max = Math.max(...entries.map(([, v]) => v));
  return (
    <ul className="space-y-1.5">
      {entries.map(([k, v]) => (
        <li key={k} className="grid grid-cols-[11rem_minmax(0,1fr)_4rem] items-center gap-2 text-xs">
          <span className="truncate font-mono text-ink-muted">{k}</span>
          <span className="h-2 bg-raised"><span className="block h-2 bg-accent/70" style={{ width: `${(v / max) * 100}%` }} /></span>
          <span className="num text-right text-ink">{count(v)}</span>
        </li>
      ))}
    </ul>
  );
}

function DataQuality({ dq }: { dq: DQ }) {
  const funnel: Record<string, number> = dq.entry_funnel ?? {};
  return (
    <Section title="Data quality" aside={<span className="text-[11px] text-ink-faint">shown on every result</span>}>
      {(dq.bias_warnings ?? []).map((w: string) => <Warning key={w}>{w}</Warning>)}
      <div className="grid gap-6 md:grid-cols-3">
        <dl className="space-y-1 text-xs text-ink-muted">
          <Row label="Coverage" value={`${ts(dq.coverage?.start)} → ${ts(dq.coverage?.end)}`} />
          <Row label="Markets usable / selected" value={`${count(dq.markets_with_usable_data)} / ${count(dq.markets_selected)}`} />
          <Row label="Bars" value={count(dq.bars_total)} />
          <Row label="Bars with no trade prints" value={pct((dq.bars_with_no_prints_pct ?? 0) / 100, { digits: 1 })} />
          <Row label="Trade prints" value={count(dq.prints_total)} />
          <Row label="Bars blocked as stale" value={count(dq.bars_blocked_stale)} />
          <Row label="Order-book coverage" value={pct((dq.orderbook_coverage_pct ?? 0) / 100, { digits: 0 })} />
          <Row label="Settled on stale data" value={count(dq.settled_on_stale_data)} />
          <Row label="Null-feature bars skipped" value={count(dq.bars_skipped_null_feature)} />
        </dl>
        <div>
          <h3 className="mb-2 text-xs uppercase tracking-wider text-ink-faint">Entry funnel</h3>
          <ul className="space-y-1 text-xs">
            {/* jsonb does not preserve key order; the funnel is a cascade, so order it explicitly. */}
            {[
              ...Object.keys(FUNNEL_LABELS).filter((k) => k in funnel),
              ...Object.keys(funnel).filter((k) => !(k in FUNNEL_LABELS)),
            ].map((k) => [k, funnel[k]] as const).map(([k, v]) => (
              <li key={k} className={`flex justify-between gap-3 ${k.startsWith("entry_rules") || k === "eligible" ? "text-ink" : "text-ink-muted"}`}>
                <span>{FUNNEL_LABELS[k] ?? k}</span>
                <span className="num">{count(v)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-2 text-xs text-ink-muted">
          <Row label="Fee mode" value={dq.fee_mode} />
          <div>
            <div className="text-ink-faint">Fee schedules applied</div>
            <div className="num mt-1 flex flex-wrap gap-1">
              {(dq.fee_schedules_applied ?? []).length
                ? dq.fee_schedules_applied.map((f: string) => <Badge key={f}>{f.split("@")[0]}</Badge>)
                : <span>none</span>}
            </div>
          </div>
          <div>
            <div className="text-ink-faint">Assumptions</div>
            <ul className="mt-1 list-disc space-y-0.5 pl-4">{(dq.assumptions ?? []).map((a: string) => <li key={a}>{a}</li>)}</ul>
          </div>
        </div>
      </div>
    </Section>
  );
}

function Manifest({ run }: { run: RunDetail }) {
  return (
    <details className="border border-line bg-surface px-4 py-2 text-xs text-ink-muted">
      <summary className="cursor-pointer select-none text-ink-faint">Reproducibility manifest</summary>
      <dl className="num mt-2 grid gap-1 md:grid-cols-2">
        <Row label="config hash" value={run.config_hash} />
        <Row label="dataset version" value={run.dataset_version_id} />
        <Row label="engine version" value={run.engine_version} />
        <Row label="seed" value={String(run.seed)} />
      </dl>
    </details>
  );
}

export function RunEmpty() {
  return <EmptyState title="No runs yet." />;
}
