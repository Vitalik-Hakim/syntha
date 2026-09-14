import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Markets" };

export default function Page() {
  return (
    <NotYet
      title="Markets"
      milestone="M1"
      description="Browse ingested Polymarket markets and build collections. Requires historical ingestion."
    />
  );
}
