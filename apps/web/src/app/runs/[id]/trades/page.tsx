import type { Metadata } from "next";

import { Blotter } from "./blotter";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: `${id} trades` };
}

export default async function TradesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Blotter id={id} />;
}
