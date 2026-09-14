"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import { apiGet, type BuiltinStrategy, type StrategySummary } from "@/lib/api/client";
import { count, ts } from "@/lib/format";

export function StrategiesList() {
  const [saved, setSaved] = useState<StrategySummary[] | null>(null);
  const [builtins, setBuiltins] = useState<BuiltinStrategy[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<StrategySummary[]>("/strategies").then(setSaved).catch((e) => setError(String(e)));
    apiGet<BuiltinStrategy[]>("/strategies/builtins").then(setBuiltins).catch(() => setBuiltins([]));
  }, []);

  return (
    <div className="max-w-6xl">
      <PageHeader title="Strategies">
        <Link href="/strategies/new" className="text-sm text-accent hover:underline">New strategy →</Link>
      </PageHeader>
      {error ? <ErrorState title="Could not load strategies" detail={error} /> : null}
      <h2 className="mb-2 text-xs uppercase tracking-wider text-ink-muted">Saved</h2>
      {saved === null ? <Loading /> : saved.length === 0 ? (
        <EmptyState title="No saved strategies yet.">Start from an example below, or build one from scratch. Every run also saves the version it used.</EmptyState>
      ) : (
        <div className="overflow-x-auto border border-line">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface text-ink-faint">
              <tr>{["Strategy", "Latest", "Versions", "Runs", "Updated"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {saved.map((s) => (
                <tr key={s.id} className="border-t border-line hover:bg-raised">
                  <td className="px-3 py-2">
                    <Link href={`/strategies/${s.id}`} className="text-sm text-ink hover:text-accent">{s.name}</Link>
                    {s.description ? <div className="line-clamp-1 max-w-xl text-ink-muted">{s.description}</div> : null}
                  </td>
                  <td className="num px-3 py-2">v{s.latest_version}</td>
                  <td className="num px-3 py-2">{count(s.versions)}</td>
                  <td className="num px-3 py-2">{count(s.runs)}</td>
                  <td className="num px-3 py-2 text-ink-muted">{ts(s.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2 className="mb-2 mt-6 text-xs uppercase tracking-wider text-ink-muted">Examples</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {builtins.map((b) => (
          <div key={b.key} className="border border-line bg-surface px-4 py-3">
            <div className="text-sm text-ink">{b.name}</div>
            <p className="mt-1 text-xs text-ink-muted">{b.description}</p>
            <div className="mt-2 flex gap-3 text-xs">
              <Link href={`/strategies/new?from=${b.key}`} className="text-accent hover:underline">Customise →</Link>
              <Link href="/simulate" className="text-ink-muted hover:text-ink">Run as is</Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
