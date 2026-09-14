import type { Metadata } from "next";

import { StrategiesList } from "./strategies-list";

export const metadata: Metadata = { title: "Strategies" };

export default function Page() {
  return <StrategiesList />;
}
