import type { Metadata } from "next";

import { CompareView } from "./compare-view";

export const metadata: Metadata = { title: "Compare" };

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function Page({ searchParams }: { searchParams: Search }) {
  const q = await searchParams;
  const ids = q.run_ids === undefined ? [] : Array.isArray(q.run_ids) ? q.run_ids : q.run_ids.split(",");
  return <CompareView runIds={ids.filter(Boolean)} />;
}
