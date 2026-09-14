import type { Metadata } from "next";

import { NearMissList } from "./near-miss-list";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: `${id} near misses` };
}

export default async function NearMissesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <NearMissList id={id} />;
}
