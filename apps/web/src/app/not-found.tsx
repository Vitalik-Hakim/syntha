import Link from "next/link";

import { PageHeader } from "@/components/page-header";

export default function NotFound() {
  return (
    <>
      <PageHeader title="Not found" />
      <p className="text-ink-muted">
        Nothing at this address.{" "}
        <Link href="/" className="text-accent hover:underline">
          Back to dashboard
        </Link>
      </p>
    </>
  );
}
