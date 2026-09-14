import { PageHeader } from "@/components/page-header";
import { SystemStatus } from "@/components/system-status";

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard">
        <span className="num text-xs text-ink-faint">M0 · skeleton</span>
      </PageHeader>
      <div className="grid max-w-4xl gap-6 md:grid-cols-[minmax(0,1fr)_18rem]">
        <section className="border border-line bg-surface px-5 py-4">
          <h2 className="text-xs font-medium uppercase tracking-wider text-ink-muted">
            What this answers
          </h2>
          <p className="mt-3 max-w-prose leading-relaxed text-ink">
            If you had started with $100 and followed a strategy across historical Polymarket
            markets — using only information available at the time and only trades that could
            actually have filled — what would the portfolio be worth?
          </p>
          <p className="mt-3 max-w-prose text-ink-muted">
            No data has been ingested yet. Historical ingestion arrives in M1; the backtest
            engine in M2.
          </p>
        </section>
        <SystemStatus />
      </div>
    </>
  );
}
