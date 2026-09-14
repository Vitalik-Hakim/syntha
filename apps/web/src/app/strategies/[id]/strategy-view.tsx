"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge, EmptyState, ErrorState, Loading, Section, Signed, Warning } from "@/components/ui";
import { apiGet, type StrategyDetail, type StrategyDiff } from "@/lib/api/client";
import { describeDsl } from "@/lib/dsl";
import { pct, ts } from "@/lib/format";

function show(v: unknown): string {
  return v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);
}

export function StrategyView({ id, highlight, unchanged }: { id: string; highlight: number | null; unchanged: boolean }) {
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [diff, setDiff] = useState<StrategyDiff | null>(null);
  const [focus, setFocus] = useState<number | null>(highlight);

  useEffect(() => {
    apiGet<StrategyDetail>(`/strategies/${id}`).then((d) => {
      setDetail(d);
      setFocus((f) => f ?? d.versions[0]?.version ?? null);
      setSelected(d.versions.slice(0, 2).map((v) => v.version).reverse());
    }).catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    if (selected.length < 2) {
      setDiff(null);
      return;
    }
    const q = new URLSearchParams();
    [...selected].sort((a, b) => a - b).forEach((v) => q.append("versions", String(v)));
    apiGet<StrategyDiff>(`/strategies/${id}/diff?${q}`).then(setDiff).catch(() => setDiff(null));
  }, [id, selected]);

  if (error) return <ErrorState title="Could not load the strategy" detail={error} />;
  if (!detail) return <Loading />;
  const current = detail.versions.find((v) => v.version === focus) ?? detail.versions[0]!;

  return (
    <div className="max-w-6xl space-y-5">
      <PageHeader title={detail.name}>
        <Link href={`/strategies/${id}/edit?version=${current.version}`} className="text-sm text-accent hover:underline">Edit (new version) →</Link>
        <Link href={`/simulate?strategy=${id}&version=${current.version}`} className="ml-4 text-sm text-ink-muted hover:text-ink">Run v{current.version}</Link>
      </PageHeader>
      {unchanged ? <Warning>Nothing changed: this document is identical to v{highlight}, so no new version was created.</Warning> : null}

      <Section title="Versions" aside={<span className="text-[11px] text-ink-faint">tick two or more to diff</span>}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-ink-faint">
              <tr>{["", "Version", "Created", "Note", "Hash", "Runs", "Latest run", "ROI", "Beat permuted"].map((h) => <th key={h} className="py-1 pr-3 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {detail.versions.map((v) => (
                <tr key={v.id} className={`border-t border-line ${v.version === current.version ? "bg-raised" : ""}`}>
                  <td className="py-1.5 pr-3">
                    <input type="checkbox" aria-label={`select v${v.version} for diff`} checked={selected.includes(v.version)}
                           onChange={(e) => setSelected((s) => (e.target.checked ? [...s, v.version] : s.filter((x) => x !== v.version)))} />
                  </td>
                  <td className="py-1.5 pr-3"><button type="button" onClick={() => setFocus(v.version)} className="num text-accent hover:underline">v{v.version}</button></td>
                  <td className="num py-1.5 pr-3 text-ink-muted">{ts(v.created_at)}</td>
                  <td className="py-1.5 pr-3 text-ink">{v.note ?? "—"}</td>
                  <td className="num py-1.5 pr-3 text-ink-faint">{v.dsl_hash.slice(0, 10)}</td>
                  <td className="num py-1.5 pr-3">{v.runs}</td>
                  <td className="py-1.5 pr-3">{v.latest_run ? <Link href={`/runs/${v.latest_run.id}`} className="num text-accent hover:underline">{v.latest_run.id}</Link> : "—"}</td>
                  <td className="py-1.5 pr-3">{v.latest_run ? <Signed value={v.latest_run.roi}>{pct(v.latest_run.roi, { signed: true })}</Signed> : "—"}</td>
                  <td className="num py-1.5 pr-3 text-ink-muted">{v.latest_run?.permutation_percentile == null ? "—" : `${Math.round(v.latest_run.permutation_percentile)}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {selected.length >= 2 ? (
        <Section title={`Diff ${[...selected].sort((a, b) => a - b).map((v) => `v${v}`).join(" → ")}`}>
          {!diff ? <Loading /> : diff.changes.length === 0 ? <p className="text-sm text-ink-muted">Identical documents.</p> : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-ink-faint"><tr><th className="py-1 pr-3 font-medium">Field</th>{diff.versions.map((v) => <th key={v} className="py-1 pr-3 font-medium">v{v}</th>)}</tr></thead>
                <tbody>
                  {diff.changes.map((c) => (
                    <tr key={c.path} className="border-t border-line">
                      <td className="py-1.5 pr-3 font-mono text-ink-muted">{c.path}</td>
                      {c.values.map((val, i) => (
                        <td key={i} className={`num py-1.5 pr-3 ${i > 0 && show(val) !== show(c.values[i - 1]) ? "text-accent" : "text-ink"}`}>{show(val)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      ) : null}

      <Section title={`v${current.version}`} aside={<Badge>{current.dsl_hash.slice(0, 12)}</Badge>}>
        <ul className="space-y-0.5 text-sm text-ink-muted">{describeDsl(current.dsl).map((l) => <li key={l}>{l}</li>)}</ul>
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-ink-faint">DSL</summary>
          <pre className="mt-1 max-h-96 overflow-auto bg-raised p-2 font-mono text-[11px] text-ink-muted">{JSON.stringify(current.dsl, null, 2)}</pre>
        </details>
        {current.runs === 0 ? <div className="mt-3"><EmptyState title="Not run yet."><Link href={`/simulate?strategy=${id}&version=${current.version}`} className="text-accent hover:underline">Run v{current.version}</Link></EmptyState></div> : null}
      </Section>
    </div>
  );
}
