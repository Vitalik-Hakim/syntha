import type { Metadata } from "next";

import { NotYet } from "@/components/not-yet";

export const metadata: Metadata = { title: "Settings" };

export default function Page() {
  return (
    <NotYet
      title="Settings"
      milestone="M3"
      description="Venue, default fee mode and execution preset."
    />
  );
}
