"use client";

import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge, Disclaimer, EmptyState, ErrorState, Loading, Signed } from "@/components/ui";
import { apiGet, type TradeDetail, type TradePage, type TradeRow } from "@/lib/api/client";
import { duration, price, shares, ts, usd } from "@/lib/format";

const STATUSES = ["FILLED", "PARTIAL", "UNEXECUTABLE", "REJECTED_MIN_SIZE"] as const;
const col = createColumnHelper<TradeRow>();

export function Blotter({ id }: { id: string }) {
  const [status, setStatus] = useState<string | null>(null);
  const [rows, setRows] = useState<TradeRow[]>([]);
  const [page, setPage] = useState<TradePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);

  const fetchPage = useCallback(async (cursor: number | null, reset: boolean) => {
    setLoading(true);
    try {
      const q = new URLSearchParams({ limit: "100" });
      if (status) q.set("fill_status", status);
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
  }, [id, status]);

  useEffect(() => {
    void fetchPage(null, true);
  }, [fetchPage]);

  const columns = useMemo(() => [
    col.accessor("seq", { header: "#", cell: (c) => <span className="num text-ink-faint">{c.getValue()}</span> }),
    col.accessor("fill_status", { header: "Fill", cell: (c) => <Badge kind={c.getValue()}>{c.getValue().replace("_", " ")}</Badge> }),
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
          <FilterChip key={s} active={status === s} onClick={() => setStatus(s)} label={s.replace("_", " ")} n={page?.counts_by_status[s] ?? 0} />
        ))}
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
                <tr key={r.id} onClick={() => setSelected(r.original.seq)}
                    className={`cursor-pointer border-t border-line hover:bg-raised ${selected === r.original.seq ? "bg-raised" : ""}`}>
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

      {selected !== null ? <TradeDrawer id={id} seq={selected} onClose={() => setSelected(null)} /> : null}
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

function TradeDrawer({ id, seq, onClose }: { id: string; seq: number; onClose: () => void }) {
  const [trade, setTrade] = useState<TradeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setTrade(null);
    apiGet<TradeDetail>(`/backtests/${id}/trades/${seq}`).then(setTrade).catch((e) => setError(String(e)));
  }, [id, seq]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const clauses = (trade?.entry_rule_trace?.clauses ?? []) as Array<{ feature: string; op: string; threshold: number; actual: number | null; passed: boolean; params?: Record<string, number> }>;
  return (
    <aside className="fixed inset-y-0 right-0 z-10 w-full max-w-lg overflow-y-auto border-l border-line bg-surface px-5 py-4 shadow-none">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">Trade <span className="num">#{seq}</span></h2>
        <button type="button" onClick={onClose} className="text-xs text-ink-muted hover:text-ink">Close (Esc)</button>
      </div>
      {error ? <ErrorState title="Could not load trade" detail={error} /> : null}
      {!trade ? <Loading /> : (
        <div className="space-y-4 text-xs">
          <p className="text-sm text-ink">{trade.question}</p>
          <div className="flex gap-2"><Badge kind={trade.fill_status}>{trade.fill_status}</Badge>{trade.exit_reason ? <Badge>{trade.exit_reason}</Badge> : null}</div>
          <div>
            <h3 className="mb-1 uppercase tracking-wider text-ink-faint">Why it entered</h3>
            <ul className="space-y-1">
              {clauses.map((c, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span className={c.passed ? "text-gain" : "text-loss"} aria-hidden>{c.passed ? "✓" : "✗"}</span>
                  <span className="font-mono text-ink">{c.feature}{c.params ? `(${Object.entries(c.params).map(([k, v]) => `${k}=${v}`).join(", ")})` : ""}</span>
                  <span className="text-ink-muted">{c.op} {c.threshold}</span>
                  <span className="num ml-auto text-ink">{c.actual === null ? "null" : Number(c.actual).toFixed(4)}</span>
                </li>
              ))}
            </ul>
          </div>
          <details open>
            <summary className="cursor-pointer text-ink-faint">Entry trace (sizing and execution)</summary>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap bg-raised p-2 font-mono text-[11px] text-ink-muted">{JSON.stringify({ sizing: trade.entry_rule_trace.sizing, execution: trade.entry_rule_trace.execution, attempts: trade.entry_rule_trace.attempts, attempt_refusals: trade.entry_rule_trace.attempt_refusals }, null, 2)}</pre>
          </details>
          {trade.exit_rule_trace ? (
            <details>
              <summary className="cursor-pointer text-ink-faint">Exit trace</summary>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap bg-raised p-2 font-mono text-[11px] text-ink-muted">{JSON.stringify(trade.exit_rule_trace, null, 2)}</pre>
            </details>
          ) : null}
        </div>
      )}
    </aside>
  );
}
