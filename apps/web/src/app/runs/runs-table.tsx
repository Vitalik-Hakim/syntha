"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { Badge, EmptyState, ErrorState, Loading, Signed } from "@/components/ui";
import { apiGet, type RunList } from "@/lib/api/client";
import { count, pct, ts, usd } from "@/lib/format";

export function RunsTable({ limit = 50, compact = false }: { limit?: number; compact?: boolean }) {
  const [data, setData] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<RunList>(`/backtests?limit=${limit}`).then(setData).catch((e) => setError(String(e)));
  }, [limit]);

  const body = error ? <ErrorState title="Could not load runs" detail={error} />
    : !data ? <Loading label="Loading runs…" />
    : data.runs.length === 0 ? (
      <EmptyState title="No backtests yet.">
        <Link className="text-accent hover:underline" href="/simulate">Configure one in the simulator →</Link>
      </EmptyState>
    ) : (
      <div className="overflow-x-auto border border-line">
        <table className="w-full text-left text-xs">
          <thead className="bg-surface text-ink-faint">
            <tr>{["Run", "Status", "Strategy", "Collection", "Execution", "Signals → filled", "Trades", "Net P&L", "ROI", "Created"].map((h) => <th key={h} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {data.runs.map((r) => (
              <tr key={r.id} className="border-t border-line hover:bg-raised">
                <td className="px-2 py-1.5"><Link href={`/runs/${r.id}`} className="num text-accent hover:underline">{r.id}</Link></td>
                <td className="px-2 py-1.5"><Badge kind={r.status}>{r.status}</Badge></td>
                <td className="px-2 py-1.5 text-ink">{r.strategy_name}</td>
                <td className="px-2 py-1.5 text-ink-muted">{r.collection ?? "all"}</td>
                <td className="px-2 py-1.5 font-mono text-[11px] text-ink-muted">{r.execution_model}</td>
                <td className="num px-2 py-1.5">{count(r.signals_generated)} → {count(r.signals_filled)}</td>
                <td className="num px-2 py-1.5">{count(r.n_trades)}{r.n_trades !== null && r.n_trades < 30 ? <span className="ml-1 text-warn" title="Too few trades to distinguish from luck">*</span> : null}</td>
                <td className="px-2 py-1.5"><Signed value={r.net_profit}>{usd(r.net_profit, { signed: true })}</Signed></td>
                <td className="px-2 py-1.5"><Signed value={r.roi}>{pct(r.roi, { signed: true })}</Signed></td>
                <td className="num px-2 py-1.5 text-ink-muted">{ts(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );

  if (compact) return body;
  return (
    <div className="max-w-7xl">
      <PageHeader title="Runs"><Link href="/simulate" className="text-sm text-accent hover:underline">New backtest →</Link></PageHeader>
      <p className="mb-3 text-xs text-ink-faint">Every run is immutable. * marks runs with fewer than 30 trades.</p>
      {body}
    </div>
  );
}
