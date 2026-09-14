import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Strategies" };

export default function Page() {
  return (
    <NotYet
      title="Strategies"
      milestone="M3 / M6"
      description="Versioned strategy DSL documents. The visual builder that emits them lands in M6."
    />
  );
}
