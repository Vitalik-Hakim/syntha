import type { Metadata } from "next";

import { RunView } from "./run-view";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: id };
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RunView id={id} />;
}
