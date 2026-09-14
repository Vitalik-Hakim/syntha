import type { Metadata } from "next";

import { EditStrategy } from "./edit-strategy";

export const metadata: Metadata = { title: "Edit strategy" };

type Search = Promise<Record<string, string | string[] | undefined>>;

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Search }) {
  const { id } = await params;
  const q = await searchParams;
  return <EditStrategy id={id} version={typeof q.version === "string" ? Number(q.version) : null} />;
}
