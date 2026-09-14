import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Simulate" };

export default function Page() {
  return (
    <NotYet
      title="Simulate"
      milestone="M3"
      description="Configure and queue a backtest: strategy, collection, execution preset, fees."
    />
  );
}
