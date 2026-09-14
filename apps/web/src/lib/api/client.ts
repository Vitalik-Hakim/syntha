/**
 * Typed access to the Syntha API. Types are generated from the FastAPI OpenAPI schema
 * (`pnpm gen:api`); never hand-write a response shape here.
 *
 * The browser formats what the API returns. It must not compute metrics (PRD §0 rule 3).
 */
import type { components } from "./schema";

type S = components["schemas"];
export type BacktestRequest = S["BacktestRequest-Input"];
export type BuiltinStrategy = S["BuiltinStrategy"];
export type CollectionInfo = S["CollectionInfo"];
export type PresetInfo = S["PresetInfo"];
export type RunDetail = S["RunDetail"];
export type RunSummary = S["RunSummary"];
export type EquitySeries = S["EquitySeries"];
export type TradePage = S["TradePage"];
export type TradeRow = S["TradeRow"];
export type TradeDetail = S["TradeDetail"];
export type ValidationResult = S["ValidationResult"];
export type StrategyDSL = S["StrategyDSL"];
export type RunList = S["RunList"];
export type Replay = S["Replay"];
export type ReplayMarker = S["ReplayMarker"];
export type NearMissPage = S["NearMissPage"];
export type NearMissRow = S["NearMissRow"];
export type NearMissDetail = S["NearMissDetail"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).join(".")}: ${d.msg ?? ""}`)
        .join("; ");
    }
    return JSON.stringify(body);
  } catch {
    return res.statusText;
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: "no-store", signal });
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, await detail(res));
  return (await res.json()) as T;
}
