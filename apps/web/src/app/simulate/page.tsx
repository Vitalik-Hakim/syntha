import type { Metadata } from "next";

import { SimulateWizard } from "./wizard";

export const metadata: Metadata = { title: "Simulate" };

export default function SimulatePage() {
  return <SimulateWizard />;
}
