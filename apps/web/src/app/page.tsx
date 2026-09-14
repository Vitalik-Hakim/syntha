import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { SystemStatus } from "@/components/system-status";

import { RunsTable } from "./runs/runs-table";

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard">
        <Link href="/simulate" className="text-sm text-accent hover:underline">New backtest →</Link>
      </PageHeader>
      <div className="grid max-w-7xl gap-6 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="space-y-4">
          <section className="border border-line bg-surface px-5 py-4">
            <h2 className="text-xs font-medium uppercase tracking-wider text-ink-muted">What this answers</h2>
            <p className="mt-3 max-w-prose leading-relaxed text-ink">
              If you had started with $100 and followed a strategy across historical Polymarket markets, using only
              information available at the time and only trades that could actually have filled, what would the
              portfolio be worth?
            </p>
          </section>
          <div>
            <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-muted">Recent runs</h2>
            <RunsTable limit={10} compact />
          </div>
        </div>
        <SystemStatus />
      </div>
    </>
  );
}
