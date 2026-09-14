import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Data" };

export default function Page() {
  return (
    <NotYet
      title="Data"
      milestone="M1"
      description="Dataset versions, coverage, gaps and recorder status."
    />
  );
}
