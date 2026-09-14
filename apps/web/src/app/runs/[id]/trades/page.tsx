import type { Metadata } from "next";

import { Blotter } from "./blotter";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: `${id} trades` };
}

export default async function TradesPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : null);
  return <Blotter id={id} initialStatus={one(query.fill_status)} initialExitReason={one(query.exit_reason)} />;
}
