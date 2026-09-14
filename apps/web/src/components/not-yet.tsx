import { PageHeader } from "@/components/page-header";

/** Empty state for routes whose milestone has not landed. Says exactly when it will. */
export function NotYet({
  title,
  milestone,
  description,
}: {
  title: string;
  milestone: string;
  description: string;
}) {
  return (
    <>
      <PageHeader title={title} />
      <section className="border border-dashed border-line px-6 py-10">
        <p className="font-mono text-xs uppercase tracking-wider text-ink-faint">
          Not built yet · {milestone}
        </p>
        <p className="mt-2 max-w-prose text-ink-muted">{description}</p>
      </section>
    </>
  );
}
