import type { Metadata } from "next";

import { SimulateWizard } from "./wizard";

export const metadata: Metadata = { title: "Simulate" };

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function SimulatePage({ searchParams }: { searchParams: Search }) {
  const q = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : null);
  const version = one(q.version);
  return <SimulateWizard fork={one(q.fork)} strategyId={one(q.strategy)} version={version ? Number(version) : null} />;
}
