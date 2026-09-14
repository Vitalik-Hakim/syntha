"use client";

import Link from "next/link";
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
  type RunDetail,
  type StrategyDetail,
  type StrategyDSL,
  type StrategySummary,
} from "@/lib/api/client";
import { describeDsl } from "@/lib/dsl";
import { count, usd } from "@/lib/format";

type CreateResponse = { run_id: string; status: string };
type CreateGroupResponse = { group_id: string; runs: { run_id: string; preset: string | null }[] };

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

type Source =
  | { kind: "builtin"; key: string }
  | { kind: "saved"; id: string; version: number }
  | { kind: "fork"; runId: string };

type PresetChoice = PresetInfo["name"] | "PARENT";

export function SimulateWizard({ fork, strategyId, version }: { fork?: string | null; strategyId?: string | null; version?: number | null }) {
  const router = useRouter();
  const [saved, setSaved] = useState<StrategySummary[]>([]);
  const [savedDetail, setSavedDetail] = useState<StrategyDetail | null>(null);
  const [forkRun, setForkRun] = useState<RunDetail | null>(null);
  const [source, setSource] = useState<Source>(
    fork ? { kind: "fork", runId: fork } : strategyId ? { kind: "saved", id: strategyId, version: version ?? 0 } : { kind: "builtin", key: "underdog_probability_reversion" },
  );
  const [tab, setTab] = useState<"examples" | "saved">(strategyId ? "saved" : "examples");
  const [builtins, setBuiltins] = useState<BuiltinStrategy[] | null>(null);
  const [collections, setCollections] = useState<CollectionInfo[] | null>(null);
  const [presets, setPresets] = useState<PresetInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [collection, setCollection] = useState<string | null>(null);
  const [preset, setPreset] = useState<PresetChoice>("REALISTIC");
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

  useEffect(() => {
    apiGet<StrategySummary[]>("/strategies").then(setSaved).catch(() => setSaved([]));
  }, []);

  // Fork: prefill everything from the parent run's configuration.
  useEffect(() => {
    if (!fork) return;
    apiGet<RunDetail>(`/backtests/${fork}`).then((run) => {
      setForkRun(run);
      const c = run.config;
      setCollection(c.dataset?.collection ?? null);
      setCapital(String(c.starting_capital));
      setBarInterval(c.bar_interval_minutes ?? 1);
      setLag(c.decision_lag_bars ?? 1);
      setSeed(String(c.seed ?? 0));
      setPreset(run.preset && run.preset !== "CUSTOM" ? (run.preset as PresetInfo["name"]) : "PARENT");
    }).catch((e) => setLoadError(String(e)));
  }, [fork]);

  useEffect(() => {
    if (source.kind !== "saved") return;
    apiGet<StrategyDetail>(`/strategies/${source.id}`).then(setSavedDetail).catch((e) => setLoadError(String(e)));
  }, [source]);

  const strategy: { name: string; description: string | null | undefined; dsl: StrategyDSL; label: string } | null = useMemo(() => {
    if (source.kind === "builtin") {
      const b = builtins?.find((x) => x.key === source.key);
      return b ? { name: b.name, description: b.description, dsl: b.dsl, label: "example" } : null;
    }
    if (source.kind === "fork") {
      return forkRun ? { name: forkRun.config.strategy.name, description: forkRun.config.strategy.description, dsl: forkRun.config.strategy as StrategyDSL, label: `from ${forkRun.id}` } : null;
    }
    const v = savedDetail?.versions.find((x) => x.version === source.version) ?? savedDetail?.versions[0];
    return v ? { name: v.dsl.name, description: v.dsl.description, dsl: v.dsl, label: `saved v${v.version}` } : null;
  }, [source, builtins, forkRun, savedDetail]);

  const selectedCollection = collections?.find((c) => c.name === collection) ?? null;
  const selectedPreset =
    preset === "PARENT" && forkRun
      ? { name: "PARENT", label: "Parent's settings", execution: forkRun.config.execution, fees: forkRun.config.fees }
      : presets?.find((p) => p.name === preset) ?? null;
  const editHref =
    source.kind === "saved" ? `/strategies/${source.id}/edit` : source.kind === "fork" ? `/strategies/new?fork=${source.runId}` : `/strategies/new?from=${source.key}`;

  const survivorship = useMemo(() => {
    const sel = selectedCollection?.definition?.selection_uses_final_volume as
      | { min_lifetime_volume: number; survivorship_free_for_min_volume_asof_at_least: number } | null | undefined;
    if (!sel || !strategy) return null;
    const asof = strategy.dsl.universe?.min_volume_asof ?? null;
    if (asof !== null && asof >= sel.survivorship_free_for_min_volume_asof_at_least) return null;
    return `This collection was pre-filtered on final volume (≥ ${usd(sel.min_lifetime_volume, { digits: 0 })}). ${strategy.name} uses ${asof === null ? "no" : usd(asof, { digits: 0 })} as-of volume filter, below the ${usd(sel.survivorship_free_for_min_volume_asof_at_least, { digits: 0 })} needed to avoid survivorship bias. Results will carry a warning.`;
  }, [selectedCollection, strategy]);

  async function submit(allPresets: boolean) {
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
      execution: selectedPreset.execution as BacktestRequest["execution"],
      fees: (selectedPreset.fees ?? { mode: "HISTORICAL" }) as BacktestRequest["fees"],
    };
    const parent = source.kind === "fork" ? `?parent=${encodeURIComponent(source.runId)}` : "";
    try {
      if (allPresets) {
        // PRD §11.6: the default view is Realistic, with the other two alongside
        const group = await apiPost<CreateGroupResponse>(`/backtests/presets${parent}`, request);
        const realistic = group.runs.find((r) => r.preset === "REALISTIC") ?? group.runs[0]!;
        router.push(`/runs/${realistic.run_id}`);
        return;
      }
      const created = await apiPost<CreateResponse>(`/backtests${parent}`, request);
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
            {forkRun ? (
              <p className="mb-3 border-l-2 border-accent bg-raised px-3 py-2 text-xs text-ink-muted">
                Forking <Link href={`/runs/${forkRun.id}`} className="num text-accent hover:underline">{forkRun.id}</Link>: every setting starts from that run. Change what you want to test; the new run records its parent so the two can be compared.
              </p>
            ) : null}
            <div className="mb-2 flex gap-2 text-xs">
              {(["examples", "saved"] as const).map((t) => (
                <button key={t} type="button" onClick={() => setTab(t)} aria-pressed={tab === t}
                        className={`border px-2 py-1 ${tab === t ? "border-accent text-ink" : "border-line text-ink-muted hover:text-ink"}`}>
                  {t === "examples" ? "Examples" : `Saved (${saved.length})`}
                </button>
              ))}
              <Link href="/strategies/new" className="ml-auto self-center text-ink-muted hover:text-ink">New strategy →</Link>
            </div>
            {tab === "examples" ? (
              <div className="grid gap-2 md:grid-cols-2">
                {builtins.map((b) => (
                  <label key={b.key} className={`cursor-pointer border px-3 py-2 ${source.kind === "builtin" && source.key === b.key ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                    <input type="radio" name="strategy" className="sr-only" checked={source.kind === "builtin" && source.key === b.key} onChange={() => setSource({ kind: "builtin", key: b.key })} />
                    <div className="text-sm text-ink">{b.name}</div>
                    <div className="mt-1 line-clamp-2 text-xs text-ink-muted">{b.description}</div>
                  </label>
                ))}
              </div>
            ) : saved.length === 0 ? (
              <p className="text-xs text-ink-muted">No saved strategies yet. <Link href="/strategies/new" className="text-accent hover:underline">Build one</Link> or customise an example.</p>
            ) : (
              <div className="grid gap-2 md:grid-cols-2">
                {saved.map((st) => (
                  <label key={st.id} className={`cursor-pointer border px-3 py-2 ${source.kind === "saved" && source.id === st.id ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                    <input type="radio" name="strategy" className="sr-only" checked={source.kind === "saved" && source.id === st.id} onChange={() => setSource({ kind: "saved", id: st.id, version: st.latest_version })} />
                    <div className="flex items-baseline justify-between gap-2 text-sm text-ink">{st.name}<span className="num text-[11px] text-ink-faint">v{st.latest_version}</span></div>
                    <div className="mt-1 line-clamp-2 text-xs text-ink-muted">{st.description ?? `${st.runs} runs`}</div>
                  </label>
                ))}
              </div>
            )}
            {source.kind === "saved" && savedDetail && savedDetail.versions.length > 1 ? (
              <label className="mt-2 flex items-center gap-2 text-xs text-ink-muted">
                Version
                <select value={source.version || savedDetail.versions[0]!.version} onChange={(e) => setSource({ ...source, version: Number(e.target.value) })} className="border border-line bg-ground px-2 py-0.5">
                  {savedDetail.versions.map((v) => <option key={v.version} value={v.version}>v{v.version}{v.note ? ` — ${v.note}` : ""}</option>)}
                </select>
              </label>
            ) : null}
            {strategy ? (
              <div className="mt-3">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm text-ink">{strategy.name}</span>
                  <Badge>{strategy.label}</Badge>
                  <Link href={editHref} className="ml-auto text-xs text-accent hover:underline">Edit in builder →</Link>
                </div>
                <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">{describeDsl(strategy.dsl).map((l) => <li key={l}>{l}</li>)}</ul>
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-ink-faint">Strategy DSL</summary>
                  <pre className="mt-1 max-h-72 overflow-auto bg-raised p-2 font-mono text-[11px] text-ink-muted">{JSON.stringify(strategy.dsl, null, 2)}</pre>
                </details>
                <p className="mt-2 text-[11px] text-ink-faint">Example strategies are not trading recommendations.</p>
              </div>
            ) : <Loading label="Loading strategy…" />}
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
              {forkRun && forkRun.preset === "CUSTOM" ? (
                <label className={`cursor-pointer border px-3 py-2 ${preset === "PARENT" ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                  <input type="radio" name="preset" className="sr-only" checked={preset === "PARENT"} onChange={() => setPreset("PARENT")} />
                  <div className="text-sm text-ink">Parent&apos;s settings</div>
                  <div className="mt-1 text-xs text-ink-muted">The custom execution and fee settings of {forkRun.id}.</div>
                </label>
              ) : null}
              {presets.map((p) => (
                <label key={p.name} className={`cursor-pointer border px-3 py-2 ${preset === p.name ? "border-accent bg-raised" : "border-line hover:bg-raised"}`}>
                  <input type="radio" name="preset" className="sr-only" checked={preset === p.name} onChange={() => setPreset(p.name)} />
                  <div className="text-sm text-ink">{p.label}{p.name === "REALISTIC" ? <span className="ml-2 text-[11px] text-ink-faint">default</span> : null}</div>
                  <div className="mt-1 text-xs text-ink-muted">{p.description}</div>
                </label>
              ))}
            </div>
            {preset === "OPTIMISTIC" ? <div className="mt-3"><Warning>Optimistic results must never be read alone: every signal fills at the sampled price with unlimited size, which flatters execution (and magnifies a losing rule).</Warning></div> : null}
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
            {forkRun ? <Review label="Parent" value={forkRun.id} /> : null}
            <Review label="Markets" value={collection ?? "—"} />
            <Review label="Execution" value={selectedPreset?.label ?? "—"} />
            <Review label="Fees" value={selectedPreset?.fees?.mode ?? "—"} />
            <Review label="Capital" value={usd(capital)} />
            <Review label="Bars / lag" value={`${barInterval}m / ${lag}`} />
          </dl>
          {submitError ? <div className="mt-3"><ErrorState title="The API refused this run" detail={submitError} /></div> : null}
          <button type="button" disabled={submitting || !collection} onClick={() => void submit(true)}
                  className="mt-4 w-full border border-accent bg-accent/10 px-3 py-2 text-sm text-ink hover:bg-accent/20 disabled:opacity-50">
            {submitting ? "Queuing…" : "Run all three presets"}
          </button>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">
            Optimistic, Realistic and Conservative side by side, so no result is read under a single set of execution assumptions.
          </p>
          <button type="button" disabled={submitting || !collection || preset === "OPTIMISTIC"} onClick={() => void submit(false)}
                  title={preset === "OPTIMISTIC" ? "Optimistic is never run alone (PRD §11.6)" : undefined}
                  className="mt-3 w-full border border-line px-3 py-1.5 text-xs text-ink-muted hover:bg-raised hover:text-ink disabled:cursor-not-allowed disabled:opacity-50">
            {preset === "OPTIMISTIC" ? "Optimistic can only run with the other two" : `Run ${selectedPreset?.label ?? preset} only`}
          </button>
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
            Every run also computes bootstrap confidence intervals, a 200-variant permutation test, random-entry baselines and buy-and-hold on the same markets.
          </p>
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
