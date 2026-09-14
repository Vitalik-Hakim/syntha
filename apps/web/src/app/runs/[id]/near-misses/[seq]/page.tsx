import type { Metadata } from "next";

import { ReplayView } from "../../replay-view";

type Params = { params: Promise<{ id: string; seq: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { id, seq } = await params;
  return { title: `${id} near miss #${seq}` };
}

export default async function NearMissReplayPage({ params }: Params) {
  const { id, seq } = await params;
  return <ReplayView id={id} subject="near_miss" seq={Number(seq)} />;
}
