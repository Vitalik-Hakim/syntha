import type { Metadata } from "next";

import { NewStrategy } from "./new-strategy";

export const metadata: Metadata = { title: "New strategy" };

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function Page({ searchParams }: { searchParams: Search }) {
  const q = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : null);
  return <NewStrategy from={one(q.from)} fork={one(q.fork)} />;
}
