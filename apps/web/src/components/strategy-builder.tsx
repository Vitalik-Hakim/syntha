"use client";

/**
 * The visual strategy builder (PRD M6). The DSL document is the artifact; this form is a view of
 * it. Validation is the API's (`POST /api/strategies/validate`), never re-implemented here.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Badge, ErrorState, Section, Warning } from "@/components/ui";
import { apiGet, apiPost, type FeatureInfo, type SaveVersionResponse, type ValidateResult } from "@/lib/api/client";
import { describeDsl } from "@/lib/dsl";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Doc = Record<string, any>;

const OPS = [
  ["lt", "<"],
  ["lte", "≤"],
  ["gt", ">"],
  ["gte", "≥"],
  ["eq", "="],
  ["neq", "≠"],
] as const;

const EXIT_TYPES: [string, string, string][] = [
  ["TAKE_PROFIT_PCT", "Take profit", "fraction above entry, e.g. 0.3 = +30%"],
  ["STOP_LOSS_PCT", "Stop loss", "fraction below entry, e.g. 0.2 = −20%"],
  ["PRICE_ABOVE", "Price above", "absolute price"],
  ["PRICE_BELOW", "Price below", "absolute price"],
  ["MAX_HOLD_MINUTES", "Max hold", "minutes"],
  ["RULE", "Feature rule", "exit when a feature condition holds"],
];

const input = "num w-full border border-line bg-ground px-2 py-1 text-sm";

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-xs text-ink-muted">
      <span className="mb-1 block">{label}</span>
      {children}
      {hint ? <span className="mt-0.5 block text-[11px] text-ink-faint">{hint}</span> : null}
    </label>
  );
}

function numOrNull(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function ConditionEditor({ cond, features, onChange, onRemove }: { cond: Doc; features: FeatureInfo[]; onChange: (c: Doc) => void; onRemove?: () => void }) {
  const spec = features.find((f) => f.name === cond.feature);
  return (
    <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_5rem_6rem_auto] items-end gap-2">
      <Field label="Feature">
        <select
          value={cond.feature}
          onChange={(e) => {
            const next = features.find((f) => f.name === e.target.value);
            onChange({ ...cond, feature: e.target.value, params: Object.fromEntries((next?.params ?? []).map((p) => [p, cond.params?.[p] ?? 10])) });
          }}
          className="w-full border border-line bg-ground px-2 py-1 text-sm"
          title={spec?.definition}
        >
          {features.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select>
      </Field>
      <div className="flex gap-2">
        {(spec?.params ?? []).map((p) => (
          <Field key={p} label={p.replace("_minutes", " (min)")}>
            <input className={input} inputMode="numeric" value={cond.params?.[p] ?? ""} onChange={(e) => onChange({ ...cond, params: { ...cond.params, [p]: Number(e.target.value) || 0 } })} />
          </Field>
        ))}
        {spec && spec.params.length === 0 ? <span className="pb-1.5 text-[11px] text-ink-faint">no parameters</span> : null}
      </div>
      <Field label="Op">
        <select value={cond.op} onChange={(e) => onChange({ ...cond, op: e.target.value })} className="w-full border border-line bg-ground px-2 py-1 text-sm">
          {OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
      <Field label="Value">
        <input className={input} inputMode="decimal" value={cond.value} onChange={(e) => onChange({ ...cond, value: e.target.value === "" ? "" : Number.isNaN(Number(e.target.value)) ? e.target.value : Number(e.target.value) })} />
      </Field>
      {onRemove ? <button type="button" onClick={onRemove} className="mb-0.5 border border-line px-2 py-1 text-xs text-ink-muted hover:text-loss" aria-label="Remove condition">✕</button> : <span />}
      {spec ? <p className="col-span-5 -mt-1 text-[11px] text-ink-faint">{spec.definition}. Null when {spec.null_when}; a null never passes.</p> : null}
    </div>
  );
}

export function StrategyBuilder({ initial, strategyId, lockedName, origin }: { initial: Doc; strategyId?: string; lockedName?: boolean; origin?: ReactNode }) {
  const router = useRouter();
  const [doc, setDoc] = useState<Doc>(initial);
  const [features, setFeatures] = useState<FeatureInfo[]>([]);
  const [validation, setValidation] = useState<ValidateResult | null>(null);
  const [tab, setTab] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState(() => JSON.stringify(initial, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<FeatureInfo[]>("/strategies/features").then(setFeatures).catch(() => setFeatures([]));
  }, []);

  // Live validation by the API (debounced).
  useEffect(() => {
    const t = setTimeout(() => {
      apiPost<ValidateResult>("/strategies/validate", { dsl: doc }).then(setValidation).catch((e) => setValidation({ ok: false, errors: [String(e)] } as ValidateResult));
    }, 300);
    return () => clearTimeout(t);
  }, [doc]);

  const set = (patch: Doc) => setDoc((d) => ({ ...d, ...patch }));
  const entryMode: "all" | "any" | "random" = doc.entry?.random ? "random" : doc.entry?.any ? "any" : "all";
  const conditions: Doc[] = doc.entry?.[entryMode === "any" ? "any" : "all"] ?? [];
  const exits: Doc[] = doc.exit?.any ?? [];
  const u = doc.universe ?? {};
  const sizing = doc.sizing ?? {};
  const cons = doc.constraints ?? {};
  const summary = useMemo(() => {
    try {
      return validation?.ok ? describeDsl(doc as any) : [];
    } catch {
      return [];
    }
  }, [doc, validation]);

  function setConditions(next: Doc[]) {
    set({ entry: { [entryMode === "any" ? "any" : "all"]: next } });
  }

  function setExits(next: Doc[]) {
    set({ exit: { any: next } });
  }

  async function save(thenRun: boolean) {
    setSaving(true);
    setSaveError(null);
    try {
      const body = { dsl: doc, note: note || null };
      const res = strategyId
        ? await apiPost<SaveVersionResponse>(`/strategies/${strategyId}/versions`, body)
        : await apiPost<SaveVersionResponse>("/strategies", body);
      router.push(thenRun ? `/simulate?strategy=${res.strategy_id}&version=${res.version.version}` : `/strategies/${res.strategy_id}?version=${res.version.version}${res.created ? "" : "&unchanged=1"}`);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
      <div className="space-y-4">
        {origin}
        <div className="flex gap-2 text-xs">
          {(["form", "json"] as const).map((t) => (
            <button key={t} type="button" aria-pressed={tab === t}
                    onClick={() => { if (t === "json") setJsonText(JSON.stringify(doc, null, 2)); setTab(t); }}
                    className={`border px-2 py-1 ${tab === t ? "border-accent text-ink" : "border-line text-ink-muted hover:text-ink"}`}>
              {t === "form" ? "Builder" : "DSL (JSON)"}
            </button>
          ))}
        </div>

        {tab === "json" ? (
          <Section title="Strategy DSL">
            <textarea value={jsonText} onChange={(e) => setJsonText(e.target.value)} spellCheck={false}
                      className="h-[32rem] w-full border border-line bg-ground p-2 font-mono text-xs text-ink" />
            {jsonError ? <p className="mt-1 text-xs text-loss">{jsonError}</p> : null}
            <button type="button" className="mt-2 border border-line px-3 py-1 text-xs text-ink hover:bg-raised"
                    onClick={() => { try { setDoc(JSON.parse(jsonText)); setJsonError(null); setTab("form"); } catch (e) { setJsonError(String(e)); } }}>
              Apply JSON
            </button>
          </Section>
        ) : (
          <>
            <Section title="Name">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Name" hint={lockedName ? "A new version keeps the strategy's name." : "Saving under an existing name adds a version to it."}>
                  <input className="w-full border border-line bg-ground px-2 py-1 text-sm" value={doc.name ?? ""} disabled={lockedName} onChange={(e) => set({ name: e.target.value })} />
                </Field>
                <Field label="Description">
                  <input className="w-full border border-line bg-ground px-2 py-1 text-sm" value={doc.description ?? ""} onChange={(e) => set({ description: e.target.value || null })} />
                </Field>
              </div>
            </Section>

            <Section title="Universe" aside={<span className="text-[11px] text-ink-faint">which markets are eligible, bar by bar</span>}>
              <div className="grid gap-3 md:grid-cols-4">
                <Field label="Side" hint="Which outcome token to buy">
                  <select value={u.side ?? "BUY_YES"} onChange={(e) => set({ universe: { ...u, side: e.target.value } })} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                    <option value="BUY_YES">Buy YES (first outcome)</option>
                    <option value="BUY_NO">Buy NO (second outcome)</option>
                    <option value="EITHER">Either side (rules choose)</option>
                  </select>
                </Field>
                <Field label="Categories" hint="comma-separated; empty = all">
                  <input className="w-full border border-line bg-ground px-2 py-1 text-sm" value={(u.categories ?? []).join(", ")}
                         onChange={(e) => { const list = e.target.value.split(",").map((x: string) => x.trim()).filter(Boolean); set({ universe: { ...u, categories: list.length ? list : null } }); }} />
                </Field>
                <Field label="Min traded notional to date ($)" hint="as of each bar; no look-ahead">
                  <input className={input} inputMode="decimal" value={u.min_volume_asof ?? ""} onChange={(e) => set({ universe: { ...u, min_volume_asof: numOrNull(e.target.value) } })} />
                </Field>
                <Field label="Min minutes to scheduled end">
                  <input className={input} inputMode="numeric" value={u.min_time_to_resolution_minutes ?? ""} onChange={(e) => set({ universe: { ...u, min_time_to_resolution_minutes: numOrNull(e.target.value) } })} />
                </Field>
              </div>
            </Section>

            <Section title="Entry" aside={
              <select value={entryMode} className="border border-line bg-ground px-2 py-0.5 text-xs"
                      onChange={(e) => {
                        const m = e.target.value;
                        if (m === "random") set({ entry: { random: { probability_per_bar: 0.001 } } });
                        else set({ entry: { [m]: conditions.length ? conditions : [{ feature: "price", params: {}, op: "lte", value: 0.2 }] } });
                      }}>
                <option value="all">All conditions must hold</option>
                <option value="any">Any condition holds</option>
                <option value="random">Random (null hypothesis)</option>
              </select>
            }>
              {entryMode === "random" ? (
                <Field label="Probability per eligible bar">
                  <input className={input} inputMode="decimal" value={doc.entry.random.probability_per_bar} onChange={(e) => set({ entry: { random: { probability_per_bar: Number(e.target.value) } } })} />
                </Field>
              ) : (
                <div className="space-y-3">
                  {conditions.map((c, i) => (
                    <ConditionEditor key={i} cond={c} features={features}
                                     onChange={(next) => setConditions(conditions.map((x, j) => (j === i ? next : x)))}
                                     onRemove={conditions.length > 1 ? () => setConditions(conditions.filter((_, j) => j !== i)) : undefined} />
                  ))}
                  <button type="button" onClick={() => setConditions([...conditions, { feature: "price", params: {}, op: "lte", value: 0.5 }])}
                          className="border border-dashed border-line px-3 py-1 text-xs text-ink-muted hover:text-ink">+ condition</button>
                </div>
              )}
            </Section>

            <Section title="Sizing">
              <div className="grid gap-3 md:grid-cols-4">
                <Field label="Mode">
                  <select value={sizing.mode} onChange={(e) => set({ sizing: { ...sizing, mode: e.target.value } })} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                    <option value="PCT_OF_EQUITY">% of equity</option>
                    <option value="PCT_OF_INITIAL">% of starting capital</option>
                    <option value="FIXED_CASH">Fixed cash ($)</option>
                    <option value="FIXED_SHARES">Fixed shares</option>
                  </select>
                </Field>
                <Field label="Value" hint={sizing.mode?.startsWith("PCT") ? "fraction, e.g. 0.05 = 5%" : undefined}>
                  <input className={input} inputMode="decimal" value={sizing.value ?? ""} onChange={(e) => set({ sizing: { ...sizing, value: Number(e.target.value) } })} />
                </Field>
                <Field label="Max notional per trade ($)">
                  <input className={input} inputMode="decimal" value={sizing.max_notional_per_trade ?? ""} onChange={(e) => set({ sizing: { ...sizing, max_notional_per_trade: numOrNull(e.target.value) } })} />
                </Field>
                <Field label="Below the market minimum" hint="ROUND_UP places the minimum order when cash, exposure and liquidity allow">
                  <select value={sizing.min_order_policy ?? "SKIP"} onChange={(e) => set({ sizing: { ...sizing, min_order_policy: e.target.value } })} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                    <option value="SKIP">Skip (reject)</option>
                    <option value="ROUND_UP">Round up to minimum</option>
                  </select>
                </Field>
              </div>
            </Section>

            <Section title="Exits" aside={<span className="text-[11px] text-ink-faint">checked in order; the first that holds names the exit. None = hold to resolution</span>}>
              <div className="space-y-2">
                {exits.map((x, i) => (
                  <div key={i} className="border border-line px-3 py-2">
                    <div className="flex items-end gap-2">
                      <span className="num pb-1.5 text-xs text-ink-faint">{i + 1}</span>
                      <Field label="Type">
                        <select value={x.type} className="border border-line bg-ground px-2 py-1 text-sm"
                                onChange={(e) => setExits(exits.map((y, j) => (j === i ? (e.target.value === "RULE" ? { type: "RULE", condition: { feature: "price", params: {}, op: "gte", value: 0.9 } } : { type: e.target.value, value: e.target.value === "MAX_HOLD_MINUTES" ? 120 : 0.2 }) : y)))}>
                          {EXIT_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </select>
                      </Field>
                      {x.type !== "RULE" ? (
                        <Field label={EXIT_TYPES.find((t) => t[0] === x.type)?.[2] ?? "value"}>
                          <input className={input} inputMode="decimal" value={x.value} onChange={(e) => setExits(exits.map((y, j) => (j === i ? { ...y, value: Number(e.target.value) } : y)))} />
                        </Field>
                      ) : null}
                      <span className="ml-auto flex gap-1 pb-0.5">
                        <button type="button" disabled={i === 0} onClick={() => { const n = [...exits]; [n[i - 1], n[i]] = [n[i]!, n[i - 1]!]; setExits(n); }} className="border border-line px-2 py-1 text-xs text-ink-muted disabled:opacity-30" aria-label="Move up">↑</button>
                        <button type="button" disabled={i === exits.length - 1} onClick={() => { const n = [...exits]; [n[i + 1], n[i]] = [n[i]!, n[i + 1]!]; setExits(n); }} className="border border-line px-2 py-1 text-xs text-ink-muted disabled:opacity-30" aria-label="Move down">↓</button>
                        <button type="button" onClick={() => setExits(exits.filter((_, j) => j !== i))} className="border border-line px-2 py-1 text-xs text-ink-muted hover:text-loss" aria-label="Remove exit">✕</button>
                      </span>
                    </div>
                    {x.type === "RULE" ? (
                      <div className="mt-2">
                        <ConditionEditor cond={x.condition} features={features} onChange={(c) => setExits(exits.map((y, j) => (j === i ? { ...y, condition: c } : y)))} />
                      </div>
                    ) : null}
                  </div>
                ))}
                <button type="button" onClick={() => setExits([...exits, { type: "MAX_HOLD_MINUTES", value: 120 }])}
                        className="border border-dashed border-line px-3 py-1 text-xs text-ink-muted hover:text-ink">+ exit rule</button>
              </div>
            </Section>

            <Section title="Constraints">
              <div className="grid gap-3 md:grid-cols-5">
                <Field label="Max open positions"><input className={input} inputMode="numeric" value={cons.max_open_positions ?? 5} onChange={(e) => set({ constraints: { ...cons, max_open_positions: Number(e.target.value) } })} /></Field>
                <Field label="Max total exposure" hint="fraction of equity"><input className={input} inputMode="decimal" value={cons.max_total_exposure_pct ?? 0.25} onChange={(e) => set({ constraints: { ...cons, max_total_exposure_pct: Number(e.target.value) } })} /></Field>
                <Field label="Max positions per event"><input className={input} inputMode="numeric" value={cons.max_positions_per_event ?? 1} onChange={(e) => set({ constraints: { ...cons, max_positions_per_event: Number(e.target.value) } })} /></Field>
                <Field label="Cooldown per market (min)"><input className={input} inputMode="numeric" value={cons.cooldown_minutes_per_market ?? 0} onChange={(e) => set({ constraints: { ...cons, cooldown_minutes_per_market: Number(e.target.value) } })} /></Field>
                <Field label="Re-enter same market">
                  <select value={String(cons.allow_reentry_same_market ?? true)} onChange={(e) => set({ constraints: { ...cons, allow_reentry_same_market: e.target.value === "true" } })} className="w-full border border-line bg-ground px-2 py-1 text-sm">
                    <option value="true">allowed</option>
                    <option value="false">once per market</option>
                  </select>
                </Field>
              </div>
            </Section>
          </>
        )}
      </div>

      <aside className="h-fit space-y-3 border border-line bg-surface px-4 py-3 lg:sticky lg:top-6">
        <h2 className="text-xs uppercase tracking-wider text-ink-muted">Validation</h2>
        {validation == null ? <p className="text-xs text-ink-faint">Checking…</p> : validation.ok ? (
          <div className="space-y-1 text-xs">
            <Badge kind="COMPLETE">valid</Badge>
            <p className="num text-ink-faint">hash {validation.dsl_hash?.slice(0, 12)} · lookback {validation.lookback_bars} bars</p>
            <ul className="mt-2 space-y-0.5 text-ink-muted">{summary.map((l) => <li key={l}>{l}</li>)}</ul>
          </div>
        ) : (
          <ul className="space-y-1 text-xs text-loss">{(validation.errors ?? []).map((e) => <li key={e} className="font-mono">{e}</li>)}</ul>
        )}
        {doc.sizing?.min_order_policy !== "ROUND_UP" ? (
          <Warning>Orders sized below a market&apos;s minimum are rejected. With a small bankroll, consider “Round up to minimum”.</Warning>
        ) : null}
        <Field label={strategyId ? "What changed in this version" : "Note"}>
          <input className="w-full border border-line bg-ground px-2 py-1 text-sm" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. tighter stop loss" />
        </Field>
        {saveError ? <ErrorState title="Could not save" detail={saveError} /> : null}
        <button type="button" disabled={saving || !validation?.ok} onClick={() => void save(true)}
                className="w-full border border-accent bg-accent/10 px-3 py-2 text-sm text-ink hover:bg-accent/20 disabled:opacity-50">
          {saving ? "Saving…" : strategyId ? "Save version and run" : "Save and run"}
        </button>
        <button type="button" disabled={saving || !validation?.ok} onClick={() => void save(false)}
                className="w-full border border-line px-3 py-1.5 text-xs text-ink-muted hover:bg-raised hover:text-ink disabled:opacity-50">
          {strategyId ? "Save as new version" : "Save"}
        </button>
        <p className="text-[11px] leading-relaxed text-ink-faint">Versions are immutable: saving an edit creates a new version, and runs keep the exact version they used. <Link href="/strategies" className="hover:text-ink">All strategies</Link></p>
      </aside>
    </div>
  );
}
