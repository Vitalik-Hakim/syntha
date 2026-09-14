import type { ReactNode } from "react";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-6 flex items-baseline justify-between border-b border-line pb-3">
      <h1 className="text-lg font-medium text-ink">{title}</h1>
      {children}
    </header>
  );
}
