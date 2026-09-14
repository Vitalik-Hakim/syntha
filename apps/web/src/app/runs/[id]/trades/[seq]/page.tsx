import type { Metadata } from "next";

import { ReplayView } from "../../replay-view";

type Params = { params: Promise<{ id: string; seq: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id, seq } = await params;
  return { title: `${id} trade #${seq}` };
}

export default async function TradeReplayPage({ params }: Params) {
  const { id, seq } = await params;
  return <ReplayView id={id} subject="trade" seq={Number(seq)} />;
}
