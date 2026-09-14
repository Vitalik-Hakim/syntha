import type { Metadata } from "next";

import { RunsTable } from "./runs-table";

export const metadata: Metadata = { title: "Runs" };

export default function RunsPage() {
  return <RunsTable />;
}
