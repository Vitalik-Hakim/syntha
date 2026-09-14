"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Route map from PRD §19. Dynamic children (/runs/[id], ...) live under these.
const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/markets", label: "Markets" },
  { href: "/strategies", label: "Strategies" },
  { href: "/simulate", label: "Simulate" },
  { href: "/runs", label: "Runs" },
  { href: "/compare", label: "Compare" },
  { href: "/data", label: "Data" },
  { href: "/settings", label: "Settings" },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <nav className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r border-line bg-surface">
      <div className="border-b border-line px-5 py-4">
        <span className="font-mono text-[13px] font-semibold tracking-[0.2em] text-ink">SYNTHA</span>
        <p className="mt-1 text-xs text-ink-faint">Strategy research · paper only</p>
      </div>
      <ul className="flex-1 py-2">
        {NAV.map(({ href, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`block border-l-2 px-5 py-1.5 text-[13px] ${
                  active
                    ? "border-accent bg-raised text-ink"
                    : "border-transparent text-ink-muted hover:bg-raised hover:text-ink"
                }`}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
