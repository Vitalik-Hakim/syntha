"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageHeader } from "@/components/page-header";
import { StrategyBuilder } from "@/components/strategy-builder";
import { ErrorState, Loading } from "@/components/ui";
import { apiGet, type StrategyDetail } from "@/lib/api/client";

export function EditStrategy({ id, version }: { id: string; version: number | null }) {
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    apiGet<StrategyDetail>(`/strategies/${id}`).then(setDetail).catch((e) => setError(String(e)));
  }, [id]);
  const base = detail?.versions.find((v) => v.version === version) ?? detail?.versions[0];
  return (
    <div className="max-w-6xl">
      <PageHeader title={detail ? `Edit ${detail.name}` : "Edit strategy"}>
        <Link href={`/strategies/${id}`} className="text-sm text-ink-muted hover:text-ink">Versions</Link>
      </PageHeader>
      {error ? <ErrorState title="Could not load the strategy" detail={error} /> : !detail || !base ? <Loading /> : (
        <StrategyBuilder
          initial={base.dsl as Record<string, unknown>}
          strategyId={id}
          lockedName
          origin={<p className="text-xs text-ink-muted">Editing from v{base.version}. Saving creates v{detail.versions[0]!.version + 1}; v{base.version} and its runs stay unchanged.</p>}
        />
      )}
    </div>
  );
}
