"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { StrategyBuilder } from "@/components/strategy-builder";
import { ErrorState, Loading } from "@/components/ui";
import { apiGet, type BuiltinStrategy, type RunDetail } from "@/lib/api/client";

/* eslint-disable @typescript-eslint/no-explicit-any */
const BLANK: Record<string, any> = {
  dsl_version: 1,
  name: "My strategy",
  description: null,
  universe: { side: "BUY_YES", categories: null, min_volume_asof: 10000, min_time_to_resolution_minutes: 30 },
  entry: { all: [{ feature: "price", params: {}, op: "lte", value: 0.5 }] },
  sizing: { mode: "PCT_OF_EQUITY", value: 0.05, max_notional_per_trade: 25, min_order_policy: "ROUND_UP", round_up_max_multiple: 2 },
  exit: { any: [{ type: "MAX_HOLD_MINUTES", value: 240 }] },
  constraints: { max_open_positions: 5, max_total_exposure_pct: 0.25, max_positions_per_event: 1, cooldown_minutes_per_market: 60, allow_reentry_same_market: true },
};

export function NewStrategy({ from, fork }: { from: string | null; fork: string | null }) {
  const [initial, setInitial] = useState<Record<string, any> | null>(from || fork ? null : BLANK);
  const [origin, setOrigin] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (from) {
      apiGet<BuiltinStrategy[]>("/strategies/builtins").then((all) => {
        const b = all.find((x) => x.key === from);
        if (!b) throw new Error(`unknown example ${from}`);
        setInitial({ ...b.dsl, name: `${b.name} (custom)` });
        setOrigin(`Starting from the example “${b.name}”.`);
      }).catch((e) => setError(String(e)));
    } else if (fork) {
      apiGet<RunDetail>(`/backtests/${fork}`).then((run) => {
        setInitial(run.config.strategy as Record<string, any>);
        setOrigin(`Starting from the strategy of ${run.id}. Saving under the same name adds a version.`);
      }).catch((e) => setError(String(e)));
    }
  }, [from, fork]);

  return (
    <div className="max-w-6xl">
      <PageHeader title="New strategy">
        <Link href="/strategies" className="text-sm text-ink-muted hover:text-ink">All strategies</Link>
      </PageHeader>
      {error ? <ErrorState title="Could not load the starting point" detail={error} /> : !initial ? <Loading /> : (
        <StrategyBuilder initial={initial} origin={origin ? <p className="text-xs text-ink-muted">{origin}</p> : null} />
      )}
    </div>
  );
}
