"use client";

import { useCallback, useEffect, useState } from "react";

// Mirrors syntha_contracts.health. Replace with generated types once contract codegen lands (M3).
type Dependency = { name: string; ok: boolean; detail: string | null };
type Readiness = { status: "ready" | "degraded"; dependencies: Dependency[] };
type Health = { status: "ok"; service: string; version: string; engine_version: string };

type State =
  | { kind: "loading" }
  | { kind: "unreachable"; message: string }
  | { kind: "loaded"; health: Health; readiness: Readiness };

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { cache: "no-store" });
  // /health/ready returns 503 with a valid body when a dependency is down.
  if (!res.ok && res.status !== 503) throw new Error(`${path} → HTTP ${res.status}`);
  return (await res.json()) as T;
}

export function SystemStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [health, readiness] = await Promise.all([
        fetchJson<Health>("/api/health"),
        fetchJson<Readiness>("/api/health/ready"),
      ]);
      setState({ kind: "loaded", health, readiness });
    } catch (err) {
      setState({ kind: "unreachable", message: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className="border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <h2 className="text-xs font-medium uppercase tracking-wider text-ink-muted">System</h2>
        <button
          type="button"
          onClick={() => void load()}
          className="text-xs text-ink-faint hover:text-ink"
        >
          Refresh
        </button>
      </div>
      <div className="px-4 py-3">
        {state.kind === "loading" && <p className="text-ink-faint">Checking services…</p>}

        {state.kind === "unreachable" && (
          <div>
            <StatusLine label="API" ok={false} detail="unreachable" />
            <p className="mt-2 num text-xs text-ink-faint">{state.message}</p>
            <p className="mt-2 text-xs text-ink-muted">
              Start the backend with <code className="num text-ink">make dev</code>.
            </p>
          </div>
        )}

        {state.kind === "loaded" && (
          <dl className="space-y-1">
            <StatusLine label="API" ok detail={`v${state.health.version}`} />
            <StatusLine label="Engine" ok detail={`v${state.health.engine_version}`} />
            {state.readiness.dependencies.map((d) => (
              <StatusLine
                key={d.name}
                label={d.name === "postgres" ? "Postgres" : d.name === "redis" ? "Redis" : d.name}
                ok={d.ok}
                detail={d.ok ? "connected" : (d.detail ?? "down")}
              />
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}

function StatusLine({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <div className="flex items-baseline gap-3">
      {/* Shape + word, not colour alone. */}
      <span aria-hidden className={`num w-3 ${ok ? "text-gain" : "text-loss"}`}>
        {ok ? "●" : "○"}
      </span>
      <dt className="w-20 text-ink-muted">{label}</dt>
      <dd className={`num text-xs ${ok ? "text-ink" : "font-semibold text-loss"}`}>
        {ok ? "" : "DOWN · "}
        {detail}
      </dd>
    </div>
  );
}
