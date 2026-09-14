"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { MultiEquityChart, SERIES_COLORS } from "@/components/multi-equity-chart";
import { PageHeader } from "@/components/page-header";
import { RunsTable } from "@/app/runs/runs-table";
import { Badge, Disclaimer, EmptyState, ErrorState, Loading, Section, Warning } from "@/components/ui";
import { apiGet, type ComparedMetric, type Comparison } from "@/lib/api/client";
import { count, pct, usd } from "@/lib/format";

function formatValue(m: ComparedMetric, v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === "inf" ? "∞" : v;
  switch (m.format) {
    case "usd":
      return usd(v, { signed: m.key !== "fees" && m.key !== "slippage" });
    case "pct":
      return pct(v, { signed: !["win_rate", "signal_fill_rate", "max_drawdown_pct"].includes(m.key), digits: 2 });
    case "count":
      return count(v);
    case "percentile":
      return `${Math.round(v)}%`;
    default:
      return v.toFixed(2);
  }
}

function Delta({ m, d }: { m: ComparedMetric; d: number | null | undefined }) {
  if (d === null || d === undefined || d === 0) return null;
  const good = m.better === null ? null : m.better === "higher" ? d > 0 : d < 0;
  const text = m.format === "pct" ? pct(d, { signed: true, digits: 2 }) : m.format === "usd" ? usd(d, { signed: true }) : m.format === "percentile" ? `${d > 0 ? "+" : "−"}${Math.abs(Math.round(d))} pts` : `${d > 0 ? "+" : "−"}${Math.abs(d).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return (
    <span className={`ml-1 text-[11px] ${good === null ? "text-ink-faint" : good ? "text-gain" : "text-loss"}`}>
      {good === null ? "" : good ? "▲ " : "▼ "}
      {text}
    </span>
  );
}

function show(v: unknown): string {
  return v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function CompareView({ runIds }: { runIds: string[] }) {
  const router = useRouter();
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (runIds.length < 2) return;
    const q = new URLSearchParams();
    runIds.forEach((id) => q.append("run_ids", id));
    setData(null);
    apiGet<Comparison>(`/compare?${q}`).then(setData).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [runIds]);

  if (runIds.length < 2) {
    return (
      <div className="max-w-7xl">
        <PageHeader title="Compare" />
        <p className="mb-3 text-sm text-ink-muted">Tick two to six runs, then compare them side by side: what changed in the configuration, and what changed in the results.</p>
        <RunsTable limit={50} selectable onCompare={(ids) => router.push(`/compare?${ids.map((i) => `run_ids=${encodeURIComponent(i)}`).join("&")}`)} />
      </div>
    );
  }
  const q = runIds.map((i) => `run_ids=${encodeURIComponent(i)}`).join("&");
  return (
    <div className="max-w-7xl">
      <Disclaimer />
      <PageHeader title="Compare">
        <a href={`/api/compare/report.md?${q}`} className="text-sm text-accent hover:underline">Research report (.md)</a>
        <Link href="/compare" className="ml-4 text-sm text-ink-muted hover:text-ink">Pick other runs</Link>
      </PageHeader>
      {error ? <ErrorState title="Could not compare these runs" detail={error} /> : !data ? <Loading label="Comparing…" /> : (
        <div className="space-y-5">
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${data.runs.length}, minmax(0, 1fr))` }}>
            {data.runs.map((r, i) => (
              <div key={r.id} className="border border-line bg-surface px-3 py-2" style={{ borderTopColor: SERIES_COLORS[i % SERIES_COLORS.length], borderTopWidth: 2 }}>
                <div className="flex items-baseline justify-between gap-2">
                  <Link href={`/runs/${r.id}`} className="num text-sm text-accent hover:underline">{r.id}</Link>
                  {i === 0 ? <Badge>baseline</Badge> : null}
                </div>
                <div className="mt-1 truncate text-xs text-ink">{r.strategy_name}</div>
                <div className="num mt-1 text-[11px] text-ink-muted">{r.collection} · {r.preset ?? "CUSTOM"} · {r.status}</div>
                {r.parent_run_id ? <div className="num text-[11px] text-ink-faint">forked from {r.parent_run_id}</div> : null}
              </div>
            ))}
          </div>
          {data.runs.some((r) => r.status !== "DONE") ? <Warning>Some runs are not finished; their results are empty.</Warning> : null}

          <Section title="What differs" aside={<span className="text-[11px] text-ink-faint">configuration fields that are not identical</span>}>
            {data.config_diff.length === 0 ? (
              <p className="text-sm text-ink-muted">Identical configurations. Any difference in results comes from the data version or the engine version.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-ink-faint"><tr><th className="py-1 pr-3 font-medium">Field</th>{data.runs.map((r) => <th key={r.id} className="num py-1 pr-3 font-medium">{r.id}</th>)}</tr></thead>
                  <tbody>
                    {data.config_diff.map((c) => (
                      <tr key={c.path} className="border-t border-line">
                        <td className="py-1.5 pr-3 font-mono text-ink-muted">{c.path}</td>
                        {c.values.map((v, i) => <td key={i} className={`num max-w-[18rem] break-words py-1.5 pr-3 ${i > 0 && show(v) !== show(c.values[0]) ? "text-accent" : "text-ink"}`}>{show(v)}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section title="Results" aside={<span className="text-[11px] text-ink-faint">deltas against the baseline, computed server-side</span>}>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-ink-faint"><tr><th className="py-1 pr-3 font-medium">Metric</th>{data.runs.map((r) => <th key={r.id} className="num py-1 pr-3 text-right font-medium">{r.id}</th>)}</tr></thead>
                <tbody className="num">
                  {data.metrics.map((m) => (
                    <tr key={m.key} className="border-t border-line">
                      <td className="py-1.5 pr-3 font-sans text-ink-muted">{m.label}</td>
                      {m.values.map((v, i) => (
                        <td key={i} className="whitespace-nowrap py-1.5 pr-3 text-right text-ink">
                          {formatValue(m, v)}
                          {i > 0 ? <Delta m={m} d={m.deltas[i]} /> : null}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[11px] text-ink-faint">A difference is only meaningful if it is larger than the runs&apos; confidence intervals. Under ~95% on the permutation test is not evidence of edge.</p>
          </Section>

          <Section title="Equity">
            {data.equity.every((e) => e.ts.length === 0) ? <EmptyState title="No equity recorded." /> : <MultiEquityChart comparison={data} />}
          </Section>
        </div>
      )}
    </div>
  );
}
