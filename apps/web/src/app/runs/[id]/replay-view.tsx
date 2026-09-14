"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  BLOCKER_LABELS,
  EntryChecklist,
  ExecutionTable,
  ExitChecklist,
  SizingTable,
  type Trace,
} from "@/components/checklist";
import { MARKER_STYLE, ReplayChart } from "@/components/replay-chart";
import { Badge, Disclaimer, EmptyState, ErrorState, Loading, Section, Signed, Stat } from "@/components/ui";
import { ApiError, apiGet, type Replay, type RunDetail, type TradeDetail } from "@/lib/api/client";
import { duration, price, shares, ts, usd } from "@/lib/format";

type Subject = "trade" | "near_miss";

const KIND_LABELS: Record<string, string> = {
  RULES_FAILED: "Rules failed",
  BLOCKED_BY_UNIVERSE: "Blocked by universe filter",
  BLOCKED_BY_CONSTRAINT: "Blocked by portfolio constraint",
};

function path(id: string, subject: Subject, seq: number): string {
  return subject === "trade" ? `/runs/${id}/trades/${seq}` : `/runs/${id}/near-misses/${seq}`;
}

export function ReplayView({ id, subject, seq }: { id: string; subject: Subject; seq: number }) {
  const router = useRouter();
  const [replay, setReplay] = useState<Replay | null>(null);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<{ status?: number; message: string } | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setReplay(null);
    setError(null);
    const endpoint = subject === "trade" ? `/backtests/${id}/trades/${seq}/replay` : `/backtests/${id}/near-misses/${seq}/replay`;
    apiGet<Replay>(endpoint, ctrl.signal)
      .then(setReplay)
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setError({ status: e instanceof ApiError ? e.status : undefined, message: e instanceof Error ? e.message : String(e) });
      });
    return () => ctrl.abort();
  }, [id, subject, seq]);

  useEffect(() => {
    apiGet<RunDetail>(`/backtests/${id}`).then(setRun).catch(() => setRun(null));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!replay || e.target instanceof HTMLInputElement || e.metaKey || e.ctrlKey) return;
      if ((e.key === "ArrowLeft" || e.key === "k") && replay.prev_seq != null) router.push(path(id, subject, replay.prev_seq));
      if ((e.key === "ArrowRight" || e.key === "j") && replay.next_seq != null) router.push(path(id, subject, replay.next_seq));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [replay, router, id, subject]);

  const listHref = subject === "trade" ? `/runs/${id}/trades` : `/runs/${id}/near-misses`;
  const listName = subject === "trade" ? "Trade blotter" : "Near misses";
  const noun = subject === "trade" ? "Trade" : "Near miss";

  return (
    <div className="max-w-6xl">
      <Disclaimer />
      <nav className="mb-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
        <Link href={`/runs/${id}`} className="num hover:text-ink">{id}</Link>
        <span aria-hidden>/</span>
        <Link href={listHref} className="hover:text-ink">{listName}</Link>
        <span aria-hidden>/</span>
        <span className="text-ink">{noun} #{seq}</span>
        <span className="ml-auto flex gap-3">
          {replay?.prev_seq != null ? <Link href={path(id, subject, replay.prev_seq)} className="hover:text-ink">← #{replay.prev_seq}</Link> : null}
          {replay?.next_seq != null ? <Link href={path(id, subject, replay.next_seq)} className="hover:text-ink">#{replay.next_seq} →</Link> : null}
        </span>
      </nav>

      {error ? (
        error.status === 404 ? (
          <EmptyState title={`${noun} #${seq} does not exist in ${id}.`}>
            <Link href={listHref} className="text-accent hover:underline">Back to the {listName.toLowerCase()}</Link>
          </EmptyState>
        ) : (
          <ErrorState title="Could not load the replay" detail={error.message} />
        )
      ) : !replay ? (
        <Loading label="Loading replay…" />
      ) : (
        <ReplayBody replay={replay} run={run} id={id} />
      )}
    </div>
  );
}

function ReplayBody({ replay, run, id }: { replay: Replay; run: RunDetail | null; id: string }) {
  const entry = run?.config.strategy.entry;
  const mode = entry ? (entry.all ? "all" : entry.any ? "any" : null) : null;
  const trade = replay.trade;
  const nm = replay.near_miss;
  const market = replay.market;

  return (
    <div className="space-y-5">
      <header className="border-b border-line pb-4">
        <h1 className="text-lg font-medium text-ink">{market.question ?? market.condition_id}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          {market.outcome_name ? <Badge>{`buys ${market.outcome_name}`}</Badge> : null}
          {trade ? <Badge kind={trade.fill_status}>{trade.fill_status.replaceAll("_", " ")}</Badge> : null}
          {trade?.exit_reason ? <Badge>{trade.exit_reason}</Badge> : null}
          {nm ? <Badge kind="PARTIAL">{KIND_LABELS[nm.kind] ?? nm.kind}</Badge> : null}
          {nm?.blocked_by ? <Badge>{BLOCKER_LABELS[nm.blocked_by] ?? nm.blocked_by}</Badge> : null}
          <span className="num">scheduled end {ts(market.end_date)}</span>
          {market.resolved_at ? (
            <span className="num" title="Shown after the fact. The strategy never saw this.">
              resolved {market.resolution} at {ts(market.resolved_at)}
            </span>
          ) : null}
        </div>
      </header>

      {trade ? <TradeStats trade={trade} /> : null}
      {nm ? <NearMissStats replay={replay} /> : null}

      <Section
        title="Replay"
        aside={<span className="num text-[11px] text-ink-faint">{ts(replay.start)} → {ts(replay.end)} UTC</span>}
      >
        {replay.prices.ts.length === 0 && replay.prints.ts.length === 0 ? (
          <EmptyState title="No price samples or prints in this window.">The decision was made on forward-filled data from earlier samples.</EmptyState>
        ) : (
          <ReplayChart replay={replay} />
        )}
      </Section>

      {trade ? <TradeExplanation trade={trade} mode={mode} /> : null}
      {nm ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Section title="Why it didn't enter">
            <EntryChecklist trace={nm.trace} mode={mode} />
          </Section>
          <Section title="What this bar was">
            <NearMissMeaning kind={nm.kind} blockedBy={nm.blocked_by} failed={nm.failed_clauses} />
          </Section>
        </div>
      ) : null}

      <Timeline replay={replay} id={id} />
      <details className="border border-line bg-surface px-4 py-2 text-xs">
        <summary className="cursor-pointer text-ink-muted">Raw traces</summary>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-ink-muted">
          {JSON.stringify(trade ? { entry_rule_trace: trade.entry_rule_trace, exit_rule_trace: trade.exit_rule_trace } : nm?.trace, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function TradeStats({ trade }: { trade: TradeDetail }) {
  const executed = trade.fill_status === "FILLED" || trade.fill_status === "PARTIAL";
  const entry = trade.entry_rule_trace as Trace;
  if (!executed) {
    const refusals: Record<string, number> = entry.attempt_refusals ?? {};
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Outcome" sub="no position, no P&L">
          <span className="font-mono text-base">{trade.refusal_reason ?? trade.fill_status}</span>
        </Stat>
        <Stat label="Attempts" sub={Object.entries(refusals).map(([k, v]) => `${k} ×${v}`).join(" · ") || undefined}>
          <span className="num">{trade.attempts ?? "—"}</span>
        </Stat>
        <Stat label="Signal" sub={`last attempt ${ts(trade.entry_ts)}`}><span className="num text-base">{ts(trade.signal_ts)}</span></Stat>
        <Stat label="Quote at attempt" sub={trade.entry_price == null ? "no trade price existed" : `reference ${price(trade.entry_ref_price)}`}>
          <span className="num">{price(trade.entry_price)}</span>
        </Stat>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
      <Stat label="Net P&L" sub={`return ${Number(trade.return_pct ?? 0) >= 0 ? "+" : "−"}${Math.abs(Number(trade.return_pct ?? 0) * 100).toFixed(1)}%`}>
        <Signed value={trade.net_pnl}>{usd(trade.net_pnl, { signed: true })}</Signed>
      </Stat>
      <Stat label="Entry" sub={`ref ${price(trade.entry_ref_price)} · ${ts(trade.entry_ts)}`}><span className="num">{price(trade.entry_price)}</span></Stat>
      <Stat label="Exit" sub={trade.exit_ts ? ts(trade.exit_ts) : "open"}><span className="num">{price(trade.exit_price)}</span></Stat>
      <Stat label="Shares" sub={`notional ${usd(trade.notional)}`}><span className="num">{shares(trade.shares)}</span></Stat>
      <Stat label="Costs" sub={`fees ${usd(trade.fees_total)} · slippage ${usd(trade.slippage_cost)}`}>
        <span className="num">{usd(-(Number(trade.fees_total ?? 0) + Number(trade.slippage_cost ?? 0)), { signed: true })}</span>
      </Stat>
      <Stat label="Held" sub={`gross ${usd(trade.gross_pnl, { signed: true })}`}><span className="num">{duration(trade.holding_seconds)}</span></Stat>
    </div>
  );
}

function NearMissStats({ replay }: { replay: Replay }) {
  const nm = replay.near_miss!;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Stat label="Bar" sub="decision made at the bar's close"><span className="num text-base">{ts(nm.ts)}</span></Stat>
      <Stat label="Failed rules" sub={nm.kind === "RULES_FAILED" ? "entry rules that did not hold" : "rules held; something else blocked it"}>
        <span className="num">{nm.failed_clauses}</span>
      </Stat>
      <Stat label="Closeness" title="Failing clauses plus their mean relative distance to the threshold. A ranking, not a quantity." sub="lower is nearer to firing">
        <span className="num">{nm.score == null ? "—" : nm.score.toFixed(3)}</span>
      </Stat>
      <Stat label="Trades nearby" sub="same token, this window">
        <span className="num">{replay.markers.filter((m) => m.kind === "ENTRY" || m.kind === "ATTEMPT").length}</span>
      </Stat>
    </div>
  );
}

function NearMissMeaning({ kind, blockedBy, failed }: { kind: string; blockedBy: string | null | undefined; failed: number }) {
  if (kind === "RULES_FAILED") {
    return (
      <p className="text-sm leading-relaxed text-ink-muted">
        The market passed every universe filter, but {failed === 1 ? "one entry rule" : `${failed} entry rules`} did not hold. This is one of the bars in this
        market that came closest to firing, kept at least an hour apart from the others. Loosening the failing threshold would have made it a signal. Check that
        against out-of-sample data before trusting it.
      </p>
    );
  }
  if (kind === "BLOCKED_BY_UNIVERSE") {
    return (
      <p className="text-sm leading-relaxed text-ink-muted">
        Every entry rule held, but the universe filter <span className="text-ink">{BLOCKER_LABELS[blockedBy ?? ""] ?? blockedBy}</span> excluded the bar. This is the
        first bar of a stretch where that happened. Filters exist to keep the strategy away from untradeable or unrepresentative markets.
      </p>
    );
  }
  return (
    <p className="text-sm leading-relaxed text-ink-muted">
      The rules and filters held, but the portfolio constraint <span className="text-ink">{BLOCKER_LABELS[blockedBy ?? ""] ?? blockedBy}</span> blocked a new
      position. This is the first bar of a stretch where that happened.
    </p>
  );
}

function TradeExplanation({ trade, mode }: { trade: TradeDetail; mode: "all" | "any" | null }) {
  const entry = trade.entry_rule_trace as Trace;
  const exit = trade.exit_rule_trace as Trace | null;
  const fills: Trace[] = exit?.fills ?? [];
  const plan: Trace[] = entry.exit_plan ?? [];
  return (
    <>
      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Why it entered" aside={<span className="num text-[11px] text-ink-faint">decision bar {ts(entry.ts)}</span>}>
          <EntryChecklist trace={entry} mode={mode} />
        </Section>
        <Section title={trade.fill_status === "FILLED" || trade.fill_status === "PARTIAL" ? "Why it exited" : "Exit"}>
          <ExitChecklist trace={exit} status={trade.fill_status} />
          {plan.length ? (
            <div className="mt-3 border-t border-line pt-2">
              <h4 className="mb-1 text-[11px] uppercase tracking-wider text-ink-faint">Exit plan set at entry</h4>
              <ul className="space-y-0.5 text-xs text-ink-muted">
                {plan.map((p, i) => (
                  <li key={i} className="flex justify-between gap-3">
                    <span className="font-mono">{p.type}</span>
                    <span className="num text-ink">
                      {p.level != null ? Number(p.level).toFixed(4) : p.deadline ? `by ${ts(p.deadline)}` : p.clause ? `${p.clause.feature} ${p.clause.op} ${p.clause.threshold}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Section>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Section title="Sizing">
          <SizingTable sizing={entry.sizing} />
        </Section>
        <Section title="Entry execution">
          <ExecutionTable execution={entry.execution} />
        </Section>
      </div>
      {fills.length ? (
        <Section title="Exit fills">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-ink-faint">
                <tr>
                  <th className="py-1 font-medium">Time</th>
                  <th className="py-1 text-right font-medium">Reference</th>
                  <th className="py-1 text-right font-medium">Fill</th>
                  <th className="py-1 text-right font-medium">Shares</th>
                  <th className="py-1 text-right font-medium">Cap</th>
                  <th className="py-1 text-right font-medium">Fee</th>
                </tr>
              </thead>
              <tbody className="num">
                {fills.map((f, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-1">{ts(f.ts)}</td>
                    <td className="py-1 text-right">{price(f.ref_price)}</td>
                    <td className="py-1 text-right">{price(f.fill_price)}</td>
                    <td className="py-1 text-right">{shares(f.shares)}</td>
                    <td className="py-1 text-right">{f.cap == null ? "—" : shares(f.cap)}</td>
                    <td className="py-1 text-right">{usd(f.fee, { digits: 5 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </>
  );
}

function Timeline({ replay, id }: { replay: Replay; id: string }) {
  return (
    <Section title="Timeline">
      <ol className="text-xs">
        {replay.markers.map((m, i) => {
          const style = MARKER_STYLE[m.kind];
          const href =
            m.ref == null ? null : m.kind === "NEAR_MISS" ? `/runs/${id}/near-misses/${m.ref}` : `/runs/${id}/trades/${m.ref}`;
          const current = (replay.subject === "trade" && m.kind !== "NEAR_MISS") || (replay.subject === "near_miss" && m.kind === "NEAR_MISS" && m.ref === replay.seq);
          return (
            <li key={i} className="grid grid-cols-[9rem_1rem_minmax(0,1fr)_5rem] items-baseline gap-2 border-t border-line py-1 first:border-t-0">
              <span className="num text-ink-muted">{ts(m.ts)}</span>
              <span style={{ color: style?.color }} aria-hidden>{style?.glyph ?? "•"}</span>
              <span className={current ? "text-ink" : "text-ink-muted"}>
                {href && !current ? <Link href={href} className="hover:text-accent hover:underline">{m.label}</Link> : m.label}
              </span>
              <span className="num text-right text-ink">{m.price == null ? "" : m.price.toFixed(4)}</span>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}
