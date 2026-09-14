/**
 * The statistical honesty layer (PRD §15) as rendered on every result. Every number comes from
 * `backtest_runs.honesty`, computed by the engine; the browser only lays it out.
 */
import Link from "next/link";

import { Badge, Section, Signed } from "@/components/ui";
import type { RunDetail, RunSummary } from "@/lib/api/client";
import { count, pct, usd } from "@/lib/format";

/* eslint-disable @typescript-eslint/no-explicit-any */
type H = Record<string, any>;

export function ciText(ci: number[] | null | undefined, digits = 1): string {
  if (!ci) return "—";
  return `${pct(ci[0], { signed: true, digits })} to ${pct(ci[1], { signed: true, digits })}`;
}

/** "Your strategy beat 62% of permuted variants", pinned in the results header (PRD §15.3). */
export function HonestyHeadline({ honesty }: { honesty: H | null | undefined }) {
  if (!honesty) {
    return (
      <div className="border border-dashed border-line px-4 py-3 text-sm text-ink-muted">
        The honesty layer was not computed for this run (it predates engine 0.5.0). Re-run the configuration to get confidence intervals and null-hypothesis tests.
      </div>
    );
  }
  const s = honesty.summary ?? {};
  const edge = Boolean(s.evidence_of_edge);
  return (
    <div className={`border-l-2 ${edge ? "border-gain" : "border-warn"} bg-raised px-4 py-3`}>
      <p className="text-base text-ink">
        {s.permutation_percentile != null ? (
          <>
            Your strategy beat <span className="num font-semibold">{Math.round(s.permutation_percentile)}%</span> of permuted variants.
          </>
        ) : (
          s.headline
        )}
      </p>
      {s.reading ? <p className="mt-0.5 text-sm text-ink-muted">{s.reading}</p> : null}
    </div>
  );
}

function Histogram({ histogram, marker, markerLabel }: { histogram: { edges: number[]; counts: number[] }; marker: number | null; markerLabel: string }) {
  const { edges, counts } = histogram;
  if (!counts.length) return <p className="text-xs text-ink-faint">No distribution.</p>;
  const w = 320;
  const h = 90;
  const lo = Math.min(edges[0]!, marker ?? edges[0]!);
  const hi = Math.max(edges[edges.length - 1]!, marker ?? edges[edges.length - 1]!);
  const span = hi - lo || 1;
  const x = (v: number) => ((v - lo) / span) * w;
  const max = Math.max(...counts);
  return (
    <figure>
      <svg viewBox={`0 0 ${w} ${h + 14}`} className="w-full" role="img" aria-label={`Distribution of variant ROI with ${markerLabel} marked`}>
        {counts.map((c, i) => {
          const x0 = x(edges[i]!);
          const x1 = x(edges[i + 1]!);
          const bh = max ? (c / max) * h : 0;
          return <rect key={i} x={x0 + 0.5} y={h - bh} width={Math.max(x1 - x0 - 1, 1)} height={bh} className="fill-ink-faint/50" />;
        })}
        {lo <= 0 && hi >= 0 ? <line x1={x(0)} x2={x(0)} y1={0} y2={h} className="stroke-line" strokeDasharray="2 2" /> : null}
        {marker != null ? (
          <>
            <line x1={x(marker)} x2={x(marker)} y1={0} y2={h} className="stroke-accent" strokeWidth={2} />
            <text x={Math.min(Math.max(x(marker), 30), w - 30)} y={h + 12} textAnchor="middle" className="fill-accent font-mono text-[10px]">{markerLabel}</text>
          </>
        ) : null}
      </svg>
      <figcaption className="num mt-1 flex justify-between text-[10px] text-ink-faint">
        <span>{pct(lo, { signed: true, digits: 1 })}</span>
        <span>ROI</span>
        <span>{pct(hi, { signed: true, digits: 1 })}</span>
      </figcaption>
    </figure>
  );
}

function Line({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="num text-right text-ink">{children}</dd>
    </div>
  );
}

function Distribution({ title, d, reading }: { title: string; d: H; reading: string }) {
  return (
    <div className="border border-line px-3 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs uppercase tracking-wider text-ink-muted">{title}</h3>
        <span className="num text-sm text-ink">{d.percentile == null ? "—" : `beat ${Math.round(d.percentile)}%`}</span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{d.note ?? d.method}</p>
      {d.iterations ? (
        <>
          <div className="mt-2"><Histogram histogram={d.histogram} marker={d.roi_real} markerLabel="this run" /></div>
          <dl className="mt-2 space-y-0.5 text-xs">
            <Line label="Variants">{count(d.iterations)}</Line>
            <Line label="Median variant ROI"><Signed value={d.roi_quantiles?.p50}>{pct(d.roi_quantiles?.p50, { signed: true, digits: 2 })}</Signed></Line>
            <Line label="Middle 90% of variants">{ciText(d.roi_quantiles ? [d.roi_quantiles.p5, d.roi_quantiles.p95] : null, 2)}</Line>
            <Line label="Median fills per variant">{d.signals_filled_median == null ? "—" : Math.round(d.signals_filled_median)}</Line>
            {d.episodes_without_alternative != null ? (
              <Line label="Signals with no other eligible market">{count(d.episodes_without_alternative)} of {count(d.episodes)}</Line>
            ) : null}
          </dl>
          <p className="mt-2 text-[11px] text-ink-faint">{reading}</p>
        </>
      ) : null}
    </div>
  );
}

/** Bootstrap, permutation test, random-entry baseline and buy-and-hold (PRD §15.2–15.3). */
export function HonestyPanel({ honesty }: { honesty: H }) {
  const boot = honesty.bootstrap ?? {};
  const bh = honesty.buy_and_hold;
  const s = honesty.summary ?? {};
  return (
    <Section title="Is this better than chance?" aside={<span className="text-[11px] text-ink-faint">seeded from the run&apos;s seed · reproducible</span>}>
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-4">
        <div className="border border-line px-3 py-3">
          <h3 className="text-xs uppercase tracking-wider text-ink-muted">Bootstrap</h3>
          <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{boot.note ?? boot.method}</p>
          <dl className="mt-3 space-y-1 text-xs">
            <Line label="ROI"><Signed value={boot.roi ?? s.roi}>{pct(boot.roi ?? s.roi, { signed: true, digits: 2 })}</Signed></Line>
            <Line label="90% CI">{ciText(boot.roi_ci90, 2)}</Line>
            <Line label="Expectancy / trade">{boot.expectancy == null ? "—" : usd(boot.expectancy, { signed: true })}</Line>
            <Line label="95% CI">{boot.expectancy_ci95 ? `${usd(boot.expectancy_ci95[0], { signed: true })} to ${usd(boot.expectancy_ci95[1], { signed: true })}` : "—"}</Line>
            <Line label="Resamples losing money">{boot.prob_loss == null ? "—" : pct(boot.prob_loss, { digits: 0 })}</Line>
            <Line label="Resamples">{count(boot.iterations)} · {count(boot.n_trades)} trades</Line>
          </dl>
          {boot.low_sample ? <p className="mt-2 text-[11px] font-medium text-warn">Fewer than 30 trades: the interval itself is unreliable.</p> : null}
          {boot.roi_ci90 && boot.roi_ci90[0] < 0 && boot.roi_ci90[1] > 0 ? (
            <p className="mt-2 text-[11px] text-ink-faint">The interval spans zero: this data cannot tell a gain from a loss.</p>
          ) : null}
        </div>
        <Distribution
          title="Permutation test"
          d={honesty.permutation ?? {}}
          reading="Same entry times, other markets in the same category. Under ~95% is not evidence that choosing these markets mattered. Variants can differ in price level, and cheap outcomes cost more to trade."
        />
        <Distribution
          title="Random entries"
          d={honesty.random_baseline ?? {}}
          reading="Same number of signals and retry windows at random eligible bars, with identical sizing, execution and exits."
        />
        <div className="border border-line px-3 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-xs uppercase tracking-wider text-ink-muted">Buy and hold</h3>
            {s.beats_buy_and_hold == null ? null : <Badge>{s.beats_buy_and_hold ? "strategy ahead" : "buy-and-hold ahead"}</Badge>}
          </div>
          {bh ? (
            <>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-faint">{bh.method}</p>
              <dl className="mt-3 space-y-1 text-xs">
                <Line label="ROI"><Signed value={bh.roi}>{pct(bh.roi, { signed: true, digits: 2 })}</Signed></Line>
                <Line label="90% CI">{ciText(bh.bootstrap?.roi_ci90, 2)}</Line>
                <Line label="Strategy − buy and hold"><Signed value={s.roi_minus_buy_and_hold}>{pct(s.roi_minus_buy_and_hold, { signed: true, digits: 2 })}</Signed></Line>
                <Line label="Net P&L"><Signed value={bh.net_profit}>{usd(bh.net_profit, { signed: true })}</Signed></Line>
                <Line label="Trades">{count(bh.n_trades)} · win rate {pct(bh.win_rate, { digits: 0 })}</Line>
                <Line label="Max drawdown">{pct(bh.max_drawdown_pct, { digits: 1 })}</Line>
                <Line label="Fees · slippage">{usd(bh.cost_attribution?.fees)} · {usd(bh.cost_attribution?.slippage)}</Line>
              </dl>
              <p className="mt-2 text-[11px] text-ink-faint">Dashed line on the equity chart. A benchmark is noisy too; read its interval.</p>
            </>
          ) : (
            <p className="mt-1 text-xs text-ink-faint">Not computed for this run.</p>
          )}
        </div>
      </div>
    </Section>
  );
}

const PRESET_LABEL: Record<string, string> = { REALISTIC: "Realistic", OPTIMISTIC: "Optimistic", CONSERVATIVE: "Conservative" };

/** PRD §11.6: Realistic first, with Optimistic and Conservative alongside. */
export function ScenarioTable({ run }: { run: RunDetail }) {
  const group: RunSummary[] = run.group ?? [];
  if (!group.length) return null;
  const rows: [string, (r: RunSummary) => React.ReactNode][] = [
    ["Status", (r) => <Badge kind={r.status}>{r.status}</Badge>],
    ["Net P&L", (r) => <Signed value={r.net_profit}>{usd(r.net_profit, { signed: true })}</Signed>],
    ["ROI", (r) => <Signed value={r.roi}>{pct(r.roi, { signed: true, digits: 2 })}</Signed>],
    ["ROI 90% CI", (r) => <span className="text-ink-muted">{ciText(r.roi_ci90, 1)}</span>],
    ["Trades", (r) => count(r.n_trades)],
    ["Signals → filled", (r) => `${count(r.signals_generated)} → ${count(r.signals_filled)}`],
    ["Beat permuted variants", (r) => (r.permutation_percentile == null ? "—" : `${Math.round(r.permutation_percentile)}%`)],
    ["Buy-and-hold ROI", (r) => <span className="text-ink-muted">{pct(r.buy_and_hold_roi, { signed: true, digits: 2 })}</span>],
  ];
  return (
    <Section title="Execution scenarios" aside={<span className="text-[11px] text-ink-faint">same strategy and data, three sets of execution assumptions</span>}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr>
              <th className="py-1 font-medium text-ink-faint" />
              {group.map((g) => (
                <th key={g.id} className={`px-3 py-1 text-right font-medium ${g.preset === "REALISTIC" ? "text-ink" : "text-ink-muted"}`}>
                  {g.id === run.id ? (
                    <span className="border-b border-accent">{PRESET_LABEL[g.preset ?? ""] ?? g.preset}</span>
                  ) : (
                    <Link href={`/runs/${g.id}`} className="hover:text-accent hover:underline">{PRESET_LABEL[g.preset ?? ""] ?? g.preset}</Link>
                  )}
                  {g.preset === "OPTIMISTIC" ? <span className="ml-1 text-[10px] font-normal text-warn" title="Every signal fills at the sampled price with unlimited size. Optimistic about fills, not necessarily about P&L: a losing rule loses more when everything fills.">fills everything</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="num">
            {rows.map(([label, cell]) => (
              <tr key={label} className="border-t border-line">
                <td className="py-1.5 font-sans text-ink-muted">{label}</td>
                {group.map((g) => (
                  <td key={g.id} className={`px-3 py-1.5 text-right ${g.id === run.id ? "bg-raised" : ""}`}>{cell(g)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
