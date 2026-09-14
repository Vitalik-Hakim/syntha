"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge, ErrorState, Loading, Warning } from "@/components/ui";
import {
  apiGet,
  apiPost,
  type BacktestRequest,
  type BuiltinStrategy,
  type CollectionInfo,
  type PresetInfo,
  type StrategyDSL,
} from "@/lib/api/client";
import { count, pct, usd } from "@/lib/format";

type CreateResponse = { run_id: string; status: string };

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="border border-line bg-surface">
      <h2 className="flex items-baseline gap-3 border-b border-line px-4 py-2 text-sm">
        <span className="num text-accent">{n}</span>
        <span className="font-medium text-ink">{title}</span>
      </h2>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

function describeDsl(dsl: StrategyDSL): string[] {
  const lines: string[] = [];
  const u = dsl.universe;
  lines.push(`Buys ${u?.side === "BUY_NO" ? "NO" : "YES"} tokens${u?.categories ? ` in ${u.categories.join(", ")}` : ""}`);
  if (u?.min_volume_asof != null) lines.push(`only after ${usd(u.min_volume_asof, { digits: 0 })} has traded (as of each bar)`);
  if (u?.min_time_to_resolution_minutes != null) lines.push(`at least ${u.min_time_to_resolution_minutes} min before the scheduled end`);
  const entry = dsl.entry;
  if (entry.random) lines.push(`enters at random with probability ${entry.random.probability_per_bar} per eligible bar`);
  const conds = entry.all ?? entry.any ?? [];
  for (const c of conds) {
    const params = c.params && Object.keys(c.params).length ? `(${Object.entries(c.params).map(([k, v]) => `${k.replace("_minutes", "")}=${v}m`).join(", ")})` : "";
    lines.push(`${entry.any ? "or" : "when"} ${c.feature}${params} ${c.op} ${c.value}`);
  }
  const s = dsl.sizing;
  lines.push(`sizes ${s.mode === "PCT_OF_EQUITY" ? `${pct(s.value, { digits: 0 })} of equity` : s.mode === "PCT_OF_INITIAL" ? `${pct(s.value, { digits: 0 })} of starting capital` : s.mode === "FIXED_CASH" ? usd(s.value) : `${s.value} shares`}${s.max_notional_per_trade ? ` (max ${usd(s.max_notional_per_trade)})` : ""}`);
  const exits = dsl.exit?.any ?? [];
  lines.push(exits.length ? `exits on ${exits.map((e) => e.type === "RULE" ? `rule(${e.condition.feature})` : `${e.type.toLowerCase()} ${e.value}`).join(", ")}` : "holds to resolution");
  return lines;
}

export function SimulateWizard() {
  const router = useRouter();
  const [builtins, setBuiltins] = useState<BuiltinStrategy[] | null>(null);
  const [collections, setCollections] = useState<CollectionInfo[] | null>(null);
  const [presets, setPresets] = useState<PresetInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [strategyKey, setStrategyKey] = useState("underdog_probability_reversion");
  const [collection, setCollection] = useState<string | null>(null);
  const [preset, setPreset] = useState<PresetInfo["name"]>("REALISTIC");
  const [capital, setCapital] = useState("100");
  const [barInterval, setBarInterval] = useState(1);
  const [lag, setLag] = useState(1);
  const [seed, setSeed] = useState("1");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      apiGet<BuiltinStrategy[]>("/strategies/builtins"),
      apiGet<CollectionInfo[]>("/collections"),
      apiGet<PresetInfo[]>("/presets"),
    ]).then(([b, c, p]) => {
      setBuiltins(b);
      setCollections(c);
      setPresets(p);
      setCollection((prev) => prev ?? c[0]?.name ?? null);
    }).catch((e) => setLoadError(String(e)));
  }, []);

  const strategy = builtins?.find((b) => b.key === strategyKey) ?? null;
  const selectedCollection = collections?.find((c) => c.name === collection) ?? null;
  const selectedPreset = presets?.find((p) => p.name === preset) ?? null;

  const survivorship = useMemo(() => {
    const sel = selectedCollection?.definition?.selection_uses_final_volume as
      | { min_lifetime_volume: number; survivorship_free_for_min_volume_asof_at_least: number } | null | undefined;
    if (!sel || !strategy) return null;
    const asof = strategy.dsl.universe?.min_volume_asof ?? null;
    if (asof !== null && asof >= sel.survivorship_free_for_min_volume_asof_at_least) return null;
    return `This collection was pre-filtered on final volume (≥ ${usd(sel.min_lifetime_volume, { digits: 0 })}). ${strategy.name} uses ${asof === null ? "no" : usd(asof, { digits: 0 })} as-of volume filter, below the ${usd(sel.survivorship_free_for_min_volume_asof_at_least, { digits: 0 })} needed to avoid survivorship bias. Results will carry a warning.`;
  }, [selectedCollection, strategy]);

  async function submit() {
    if (!strategy || !selectedPreset || !collection) return;
    setSubmitting(true);
    setSubmitError(null);
    const request: BacktestRequest = {
      strategy: strategy.dsl as BacktestRequest["strategy"],
      dataset: { collection },
      starting_capital: capital,
      seed: Number(seed) || 0,
      bar_interval_minutes: barInterval as BacktestRequest["bar_interval_minutes"],
      decision_lag_bars: lag,
      execution: selectedPreset.execution,
      fees: selectedPreset.fees as BacktestRequest["fees"],
    };
    try {
      const created = await apiPost<CreateResponse>("/backtests", request);
      router.push(`/runs/${created.run_id}`);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  }

  if (loadError) return <ErrorState title="Could not load the simulator" detail={`${loadError}\n\nIs the API running (make dev)?`} />;
  if (!builtins || !collections || !presets) return <Loading label="Loading strategies, collections and presets…" />;

  const capitalNumber = Number(capital);
  return (
    <div className="max-w-5xl">
      <PageHeader title="Simulate" />
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="space-y-4">
          <Step n={1} title="Strategy">
            <div className="grid gap-2 md:grid-cols-2">
              {builtins.map((b) => (
                <label key={b.key} className={`cursor-pointer border px-3 py-2 ${strategyKey === b.key ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                  <input type="radio" name="strategy" className="sr-only" checked={strategyKey === b.key} onChange={() => setStrategyKey(b.key)} />
                  <div className="text-sm text-ink">{b.name}</div>
                  <div className="mt-1 line-clamp-2 text-xs text-ink-muted">{b.description}</div>
                </label>
              ))}
            </div>
            {strategy ? (
              <div className="mt-3">
                <ul className="space-y-0.5 text-xs text-ink-muted">{describeDsl(strategy.dsl).map((l) => <li key={l}>{l}</li>)}</ul>
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-ink-faint">Strategy DSL</summary>
                  <pre className="mt-1 max-h-72 overflow-auto bg-raised p-2 font-mono text-[11px] text-ink-muted">{JSON.stringify(strategy.dsl, null, 2)}</pre>
                </details>
                <p className="mt-2 text-[11px] text-ink-faint">Example strategies are not trading recommendations. Editing strategies arrives with the builder (M6).</p>
              </div>
            ) : null}
          </Step>

          <Step n={2} title="Markets">
            {collections.length === 0 ? <p className="text-sm text-ink-muted">No collections yet: run <code className="num">make ingest-sample</code>, <code className="num">make ingest-fetch</code> and <code className="num">make rebuild-normalized</code>.</p> : (
              <div className="space-y-2">
                {collections.map((c) => (
                  <label key={c.name} className={`block cursor-pointer border px-3 py-2 ${collection === c.name ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                    <input type="radio" name="collection" className="sr-only" checked={collection === c.name} onChange={() => setCollection(c.name)} />
                    <div className="flex items-baseline gap-2">
                      <span className="font-mono text-sm text-ink">{c.name}</span>
                      <span className="num text-xs text-ink-muted">{count(c.markets)} markets</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">{Object.entries(c.markets_by_category).map(([k, v]) => <Badge key={k}>{k} {v}</Badge>)}</div>
                    {c.survivorship_note ? <p className="mt-1 text-xs text-warn">{c.survivorship_note}</p> : null}
                  </label>
                ))}
              </div>
            )}
            {survivorship ? <div className="mt-3"><Warning>{survivorship}</Warning></div> : null}
          </Step>

          <Step n={3} title="Execution assumptions">
            <div className="grid gap-2 md:grid-cols-3">
              {presets.map((p) => (
                <label key={p.name} className={`cursor-pointer border px-3 py-2 ${preset === p.name ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                  <input type="radio" name="preset" className="sr-only" checked={preset === p.name} onChange={() => setPreset(p.name)} />
                  <div className="text-sm text-ink">{p.label}{p.name === "REALISTIC" ? <span className="ml-2 text-[11px] text-ink-faint">default</span> : null}</div>
                  <div className="mt-1 text-xs text-ink-muted">{p.description}</div>
                </label>
              ))}
            </div>
            {preset === "OPTIMISTIC" ? <div className="mt-3"><Warning>Optimistic results must never be read alone: fills at mid with unlimited size are an upper bound.</Warning></div> : null}
            <p className="mt-2 text-[11px] text-ink-faint">Order-book execution is unavailable: it needs recorded L2 data, which only exists from when the recorder started.</p>
          </Step>

          <Step n={4} title="Capital and timing">
            <div className="grid gap-4 md:grid-cols-4">
              <Field label="Starting capital ($)">
                <input value={capital} onChange={(e) => setCapital(e.target.value)} inputMode="decimal" className="num w-full border border-line bg-ground px-2 py-1 text-sm" />
              </Field>
              <Field label="Bar interval">
                <select value={barInterval} onChange={(e) => setBarInterval(Number(e.target.value))} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                  {[1, 5, 15, 60].map((m) => <option key={m} value={m}>{m} min</option>)}
                </select>
              </Field>
              <Field label="Decision lag (bars)">
                <select value={lag} onChange={(e) => setLag(Number(e.target.value))} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                  {[0, 1, 2, 5].map((l) => <option key={l} value={l}>{l}</option>)}
                </select>
              </Field>
              <Field label="Seed">
                <input value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" className="num w-full border border-line bg-ground px-2 py-1 text-sm" />
              </Field>
            </div>
            <div className="mt-3 space-y-2">
              {capitalNumber > 0 && capitalNumber < 1000 ? (
                <p className="text-xs text-ink-muted">At {usd(capitalNumber, { digits: 0 })}, many trades fall below Polymarket&apos;s minimum order size and are rejected; the run reports how many. Consider $1,000 to see the strategy rather than the minimum size.</p>
              ) : null}
              {lag === 0 ? <Warning>Same-bar fills use the prints that triggered the signal. Results will be marked SAME-BAR FILL — OPTIMISTIC.</Warning> : null}
              {strategy && barInterval > 1 && JSON.stringify(strategy.dsl).match(/"lookback_minutes":\s*(\d+)/g)?.some((m) => Number(m.split(":")[1]) % barInterval !== 0) ? (
                <Warning>This strategy&apos;s lookbacks are not multiples of {barInterval} minutes; the API will reject the run.</Warning>
              ) : null}
            </div>
          </Step>
        </div>

        <aside className="h-fit border border-line bg-surface px-4 py-3 lg:sticky lg:top-6">
          <h2 className="text-xs uppercase tracking-wider text-ink-muted">Review</h2>
          <dl className="mt-2 space-y-1 text-xs">
            <Review label="Strategy" value={strategy?.name ?? "—"} />
            <Review label="Markets" value={collection ?? "—"} />
            <Review label="Execution" value={selectedPreset?.label ?? "—"} />
            <Review label="Fees" value={selectedPreset?.fees.mode ?? "—"} />
            <Review label="Capital" value={usd(capital)} />
            <Review label="Bars / lag" value={`${barInterval}m / ${lag}`} />
          </dl>
          {submitError ? <div className="mt-3"><ErrorState title="The API refused this run" detail={submitError} /></div> : null}
          <button type="button" disabled={submitting || !collection} onClick={() => void submit()}
                  className="mt-4 w-full border border-accent bg-accent/10 px-3 py-2 text-sm text-ink hover:bg-accent/20 disabled:opacity-50">
            {submitting ? "Queuing…" : "Run backtest"}
          </button>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">Runs are immutable and reproducible: the full configuration, dataset version and seed are recorded.</p>
        </aside>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs text-ink-muted">
      <span className="mb-1 block">{label}</span>
      {children}
    </label>
  );
}

function Review({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-faint">{label}</dt>
      <dd className="num truncate text-right text-ink">{value}</dd>
    </div>
  );
}
