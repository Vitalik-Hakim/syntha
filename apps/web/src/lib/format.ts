/** Presentation only: every value formatted here was computed by the engine or the API. */

const MINUS = "−";

export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function usd(value: string | number | null | undefined, opts: { signed?: boolean; digits?: number } = {}): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const digits = opts.digits ?? 2;
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const sign = n < 0 ? MINUS : opts.signed && n > 0 ? "+" : "";
  return `${sign}$${abs}`;
}

export function pct(value: string | number | null | undefined, opts: { signed?: boolean; digits?: number } = {}): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const digits = opts.digits ?? 2;
  const abs = (Math.abs(n) * 100).toFixed(digits);
  const sign = n < 0 ? MINUS : opts.signed && n > 0 ? "+" : "";
  return `${sign}${abs}%`;
}

export function price(value: string | number | null | undefined): string {
  const n = toNumber(value);
  return n === null ? "—" : n.toFixed(3);
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export function shares(value: string | number | null | undefined): string {
  const n = toNumber(value);
  return n === null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export function ts(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86_400).toFixed(1)}d`;
}

/** Tone for a signed value: used together with the explicit sign, never alone. */
export function tone(value: string | number | null | undefined): "gain" | "loss" | "flat" {
  const n = toNumber(value);
  if (n === null || n === 0) return "flat";
  return n > 0 ? "gain" : "loss";
}
