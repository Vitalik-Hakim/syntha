import type { Metadata } from "next";

import { StrategyView } from "./strategy-view";

export const metadata: Metadata = { title: "Strategy" };

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Search }) {
  const { id } = await params;
  const q = await searchParams;
  return <StrategyView id={id} highlight={typeof q.version === "string" ? Number(q.version) : null} unchanged={q.unchanged === "1"} />;
}
