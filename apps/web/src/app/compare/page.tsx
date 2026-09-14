import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Compare" };

export default function Page() {
  return (
    <NotYet
      title="Compare"
      milestone="M6"
      description="Side-by-side diff of two runs' configs and results."
    />
  );
}
