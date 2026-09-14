"use client";

import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Disclaimer, EmptyState, ErrorState, Loading, Signed } from "@/components/ui";
import { apiGet, type TradePage, type TradeRow } from "@/lib/api/client";
import { duration, price, shares, ts, usd } from "@/lib/format";

const STATUSES = ["FILLED", "PARTIAL", "UNEXECUTABLE", "REJECTED_MIN_SIZE"] as const;
const col = createColumnHelper<TradeRow>();

export function Blotter({ id, initialStatus = null, initialExitReason = null }: {
  id: string; initialStatus?: string | null; initialExitReason?: string | null;
}) {
  const router = useRouter();
  const [status, setStatus] = useState<string | null>(initialStatus);
  const [exitReason, setExitReason] = useState<string | null>(initialExitReason);
  const [rows, setRows] = useState<TradeRow[]>([]);
  const [page, setPage] = useState<TradePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchPage = useCallback(async (cursor: number | null, reset: boolean) => {
    setLoading(true);
    try {
      const q = new URLSearchParams({ limit: "100" });
      if (status) q.set("fill_status", status);
      if (exitReason) q.set("exit_reason", exitReason);
      if (cursor !== null) q.set("cursor", String(cursor));
      const next = await apiGet<TradePage>(`/backtests/${id}/trades?${q}`);
      setPage(next);
      setRows((prev) => (reset ? next.trades : [...prev, ...next.trades]));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [id, status, exitReason]);

  useEffect(() => {
    void fetchPage(null, true);
  }, [fetchPage]);

  const columns = useMemo(() => [
    col.accessor("seq", { header: "#", cell: (c) => <span className="num text-ink-faint">{c.getValue()}</span> }),
    col.accessor("fill_status", { header: "Fill", cell: (c) => <Badge kind={c.getValue()}>{c.getValue().replaceAll("_", " ")}</Badge> }),
    col.accessor("question", { header: "Market", cell: (c) => <span className="line-clamp-1 max-w-[22rem] text-ink" title={c.getValue() ?? ""}>{c.getValue() ?? c.row.original.condition_id}</span> }),
    col.accessor("entry_ts", { header: "Entry", cell: (c) => <span className="num text-ink-muted">{ts(c.getValue())}</span> }),
    col.accessor("entry_price", { header: "Price", cell: (c) => <span className="num">{price(c.getValue())}</span> }),
    col.accessor("shares", { header: "Shares", cell: (c) => <span className="num">{shares(c.getValue())}</span> }),
    col.accessor("exit_reason", { header: "Exit", cell: (c) => <span className="font-mono text-[11px] text-ink-muted">{c.getValue() ?? c.row.original.refusal_reason ?? "—"}</span> }),
    col.accessor("exit_price", { header: "Exit price", cell: (c) => <span className="num">{price(c.getValue())}</span> }),
    col.accessor("holding_seconds", { header: "Held", cell: (c) => <span className="num text-ink-muted">{duration(c.getValue())}</span> }),
    col.accessor("fees_total", { header: "Fees", cell: (c) => <span className="num text-ink-muted">{usd(c.getValue())}</span> }),
    col.accessor("net_pnl", { header: "Net P&L", cell: (c) => <Signed value={c.getValue()}>{usd(c.getValue(), { signed: true })}</Signed> }),
    col.accessor("attempts", { header: "Tries", cell: (c) => <span className="num text-ink-faint">{c.getValue() ?? ""}</span> }),
  ], []);

  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });

  return (
    <div className="max-w-7xl">
      <Disclaimer />
      <header className="mb-4 flex flex-wrap items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-lg font-medium">Trade blotter</h1>
        <Link href={`/runs/${id}`} className="num text-xs text-accent hover:underline">{id}</Link>
        <a className="ml-auto text-sm text-ink-muted hover:text-ink" href={`/api/backtests/${id}/export.csv`}>Export CSV</a>
      </header>

      <div className="mb-3 flex flex-wrap gap-2">
        <FilterChip active={status === null} onClick={() => setStatus(null)} label="All" n={page ? Object.values(page.counts_by_status).reduce((a, b) => a + b, 0) : undefined} />
        {STATUSES.map((s) => (
          <FilterChip key={s} active={status === s} onClick={() => setStatus(s)} label={s.replaceAll("_", " ")} n={page?.counts_by_status[s] ?? 0} />
        ))}
        {exitReason ? (
          <FilterChip active onClick={() => setExitReason(null)} label={`exit ${exitReason} ×`} />
        ) : null}
        <Link href={`/runs/${id}/near-misses`} className="ml-auto self-center text-xs text-ink-muted hover:text-ink">Near misses →</Link>
      </div>

      {error ? <ErrorState title="Could not load trades" detail={error} /> : null}
      {!error && !loading && rows.length === 0 ? (
        <EmptyState title="No trades match.">
          A run with no filled trades still records every signal it tried; check the run&apos;s entry funnel for why nothing fired.
        </EmptyState>
      ) : null}

      {rows.length ? (
        <div className="overflow-x-auto border border-line">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface text-ink-faint">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>{hg.headers.map((h) => <th key={h.id} className="whitespace-nowrap px-2 py-2 font-medium">{flexRender(h.column.columnDef.header, h.getContext())}</th>)}</tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((r) => (
                <tr key={r.id} tabIndex={0}
                    onClick={() => router.push(`/runs/${id}/trades/${r.original.seq}`)}
                    onKeyDown={(e) => e.key === "Enter" && router.push(`/runs/${id}/trades/${r.original.seq}`)}
                    title="Open the replay"
                    className="cursor-pointer border-t border-line hover:bg-raised focus:bg-raised focus:outline-none">
                  {r.getVisibleCells().map((c) => <td key={c.id} className="whitespace-nowrap px-2 py-1.5">{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="mt-3 flex items-center gap-3 text-xs text-ink-muted">
        {page ? <span className="num">{rows.length.toLocaleString()} of {page.total.toLocaleString()}</span> : null}
        {page?.next_cursor != null ? (
          <button type="button" className="border border-line px-3 py-1 text-ink hover:bg-raised" onClick={() => void fetchPage(page.next_cursor ?? null, false)} disabled={loading}>
            {loading ? "Loading…" : "Load more"}
          </button>
        ) : null}
        {loading && !rows.length ? <Loading label="Loading trades…" /> : null}
      </div>
    </div>
  );
}

function FilterChip({ label, n, active, onClick }: { label: string; n?: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
            className={`border px-2 py-1 text-xs ${active ? "border-accent text-ink" : "border-line text-ink-muted hover:text-ink"}`}>
      {label} {n !== undefined ? <span className="num text-ink-faint">{n.toLocaleString()}</span> : null}
    </button>
  );
}
