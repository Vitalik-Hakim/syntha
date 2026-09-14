import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Runs" };

export default function Page() {
  return (
    <NotYet
      title="Runs"
      milestone="M3"
      description="Every backtest run, immutable, with its dataset version, config hash and seed."
    />
  );
}
