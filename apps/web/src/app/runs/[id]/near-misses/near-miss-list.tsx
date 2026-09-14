"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { BLOCKER_LABELS, type Trace } from "@/components/checklist";
import { Badge, Disclaimer, EmptyState, ErrorState, Loading } from "@/components/ui";
import { apiGet, type NearMissPage, type NearMissRow, type RunDetail } from "@/lib/api/client";
import { count, ts } from "@/lib/format";

const KINDS: { key: string; label: string; help: string }[] = [
  { key: "RULES_FAILED", label: "Rules failed", help: "Eligible bars where the entry rules came closest to firing" },
  { key: "BLOCKED_BY_UNIVERSE", label: "Blocked by filter", help: "Entry rules held, a universe filter excluded the bar" },
  { key: "BLOCKED_BY_CONSTRAINT", label: "Blocked by constraint", help: "Rules and filters held, a portfolio constraint blocked the position" },
];

export function NearMissList({ id }: { id: string }) {
  const router = useRouter();
  const [kind, setKind] = useState<string | null>(null);
  const [blocker, setBlocker] = useState<string | null>(null);
  const [order, setOrder] = useState<"nearest" | "time">("nearest");
  const [rows, setRows] = useState<NearMissRow[]>([]);
  const [page, setPage] = useState<NearMissPage | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<RunDetail>(`/backtests/${id}`).then(setRun).catch(() => setRun(null));
  }, [id]);

  const fetchPage = useCallback(
    async (cursor: number | null, reset: boolean) => {
      setLoading(true);
      try {
        const q = new URLSearchParams({ limit: "100", order });
        if (kind) q.set("kind", kind);
        if (blocker) q.set("blocked_by", blocker);
        if (cursor !== null) q.set("cursor", String(cursor));
        const next = await apiGet<NearMissPage>(`/backtests/${id}/near-misses?${q}`);
        setPage(next);
        setRows((prev) => (reset ? next.near_misses : [...prev, ...next.near_misses]));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [id, kind, blocker, order],
  );

  useEffect(() => {
    void fetchPage(null, true);
  }, [fetchPage]);

  const info = (run?.data_quality as Trace | null | undefined)?.near_misses as Trace | undefined;
  const total = page ? Object.values(page.counts_by_kind).reduce((a, b) => a + b, 0) : undefined;

  return (
    <div className="max-w-7xl">
      <Disclaimer />
      <header className="mb-3 flex flex-wrap items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-lg font-medium">Near misses</h1>
        <Link href={`/runs/${id}`} className="num text-xs text-accent hover:underline">{id}</Link>
        <Link href={`/runs/${id}/trades`} className="ml-auto text-sm text-ink-muted hover:text-ink">Trade blotter →</Link>
      </header>
      <p className="mb-4 max-w-prose text-sm text-ink-muted">
        Why didn&apos;t it trade here? Each row is a bar where the strategy did not enter, with the checks the engine ran at that bar.
        {info?.per_market_cap ? (
          <>
            {" "}Kept per market: the {info.per_market_cap.RULES_FAILED} nearest rule failures (at least {info.separation_bars} bars apart), the first bars of{" "}
            {info.per_market_cap.BLOCKED_BY_UNIVERSE} filter-blocked and {info.per_market_cap.BLOCKED_BY_CONSTRAINT} constraint-blocked stretches.
            {info.capped ? " The run cap was reached, so this is a sample." : ""}
          </>
        ) : null}
      </p>

      {info && info.available === false ? (
        <EmptyState title="This strategy has no near misses.">{info.note}</EmptyState>
      ) : (
        <>
          <div className="mb-2 flex flex-wrap gap-2">
            <Chip active={kind === null && blocker === null} onClick={() => { setKind(null); setBlocker(null); }} label="All" n={total} />
            {KINDS.map((k) => (
              <Chip key={k.key} title={k.help} active={kind === k.key} onClick={() => { setKind(k.key); setBlocker(null); }} label={k.label} n={page?.counts_by_kind[k.key] ?? 0} />
            ))}
            <span className="ml-auto flex items-center gap-2 text-xs text-ink-muted">
              Order
              <Chip active={order === "nearest"} onClick={() => setOrder("nearest")} label="Nearest first" />
              <Chip active={order === "time"} onClick={() => setOrder("time")} label="Time" />
            </span>
          </div>
          {page && Object.keys(page.counts_by_blocker).length ? (
            <div className="mb-3 flex flex-wrap gap-2">
              {Object.entries(page.counts_by_blocker).sort((a, b) => b[1] - a[1]).map(([b, n]) => (
                <Chip key={b} active={blocker === b} onClick={() => { setBlocker(blocker === b ? null : b); setKind(null); }} label={BLOCKER_LABELS[b] ?? b} n={n} subtle />
              ))}
            </div>
          ) : null}

          {error ? <ErrorState title="Could not load near misses" detail={error} /> : null}
          {!error && !loading && rows.length === 0 ? (
            <EmptyState title="No near misses match.">
              Runs from before M4 did not record near misses. Re-run the configuration to get them.
            </EmptyState>
          ) : null}

          {rows.length ? (
            <div className="overflow-x-auto border border-line">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface text-ink-faint">
                  <tr>
                    <th className="px-2 py-2 font-medium">#</th>
                    <th className="px-2 py-2 font-medium">Kind</th>
                    <th className="px-2 py-2 font-medium">Market</th>
                    <th className="px-2 py-2 font-medium">Bar</th>
                    <th className="px-2 py-2 font-medium">Why not</th>
                    <th className="px-2 py-2 text-right font-medium" title="Failing clauses plus mean relative distance. Lower is nearer to firing.">Closeness</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const href = `/runs/${id}/near-misses/${r.seq}`;
                    return (
                      <tr
                        key={r.seq}
                        tabIndex={0}
                        onClick={() => router.push(href)}
                        onKeyDown={(e) => e.key === "Enter" && router.push(href)}
                        className="cursor-pointer border-t border-line hover:bg-raised focus:bg-raised focus:outline-none"
                      >
                        <td className="num px-2 py-1.5 text-ink-faint">{r.seq}</td>
                        <td className="px-2 py-1.5"><Badge>{KINDS.find((k) => k.key === r.kind)?.label ?? r.kind}</Badge></td>
                        <td className="px-2 py-1.5">
                          <Link href={href} className="line-clamp-1 max-w-[26rem] text-ink hover:text-accent" title={r.question ?? ""} onClick={(e) => e.stopPropagation()}>
                            {r.question ?? r.condition_id}
                          </Link>
                        </td>
                        <td className="num whitespace-nowrap px-2 py-1.5 text-ink-muted">{ts(r.ts)}</td>
                        <td className="px-2 py-1.5 text-ink-muted">
                          {r.blocked_by ? (
                            BLOCKER_LABELS[r.blocked_by] ?? r.blocked_by
                          ) : (
                            <span className="font-mono">{r.failing_features.join(", ")} <span className="font-sans text-ink-faint">failed</span></span>
                          )}
                        </td>
                        <td className="num px-2 py-1.5 text-right text-ink">{r.score == null ? "—" : r.score.toFixed(3)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="mt-3 flex items-center gap-3 text-xs text-ink-muted">
            {page ? <span className="num">{count(rows.length)} of {count(page.total)}</span> : null}
            {page?.next_cursor != null ? (
              <button type="button" className="border border-line px-3 py-1 text-ink hover:bg-raised" disabled={loading} onClick={() => void fetchPage(page.next_cursor ?? null, false)}>
                {loading ? "Loading…" : "Load more"}
              </button>
            ) : null}
            {loading && !rows.length ? <Loading label="Loading near misses…" /> : null}
          </div>
        </>
      )}
    </div>
  );
}

function Chip({ label, n, active, onClick, title, subtle = false }: {
  label: string; n?: number; active: boolean; onClick: () => void; title?: string; subtle?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      aria-pressed={active}
      className={`border px-2 py-1 text-xs ${active ? "border-accent text-ink" : subtle ? "border-dashed border-line text-ink-faint hover:text-ink" : "border-line text-ink-muted hover:text-ink"}`}
    >
      {label} {n !== undefined ? <span className="num text-ink-faint">{n.toLocaleString()}</span> : null}
    </button>
  );
}
