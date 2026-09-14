/** Plain-language summaries of strategy DSL documents. Presentation only. */
import type { StrategyDSL } from "@/lib/api/client";
import { pct, usd } from "@/lib/format";

export function describeDsl(dsl: StrategyDSL): string[] {
  const lines: string[] = [];
  const u = dsl.universe;
  lines.push(`Buys ${u?.side === "BUY_NO" ? "NO" : u?.side === "EITHER" ? "either outcome's" : "YES"} tokens${u?.categories ? ` in ${u.categories.join(", ")}` : ""}${u?.side === "EITHER" ? " (the entry rules pick the side)" : ""}`);
  if (u?.min_volume_asof != null) lines.push(`only after ${usd(u.min_volume_asof, { digits: 0 })} has traded (as of each bar)`);
  if (u?.min_time_to_resolution_minutes != null) lines.push(`at least ${u.min_time_to_resolution_minutes} min before the scheduled end`);
  const entry = dsl.entry;
  if (entry.random) lines.push(`enters at random with probability ${entry.random.probability_per_bar} per eligible bar`);
  const conds = entry.all ?? entry.any ?? [];
  for (const c of conds) {
    const params = c.params && Object.keys(c.params).length ? `(${Object.entries(c.params).map(([k, v]) => `${k.replace("_minutes", "")}=${v}m`).join(", ")})` : "";
    lines.push(`${entry.any ? "or" : "when"} ${c.feature}${params} ${c.op} ${c.value}`);
  }
  const s = dsl.sizing;
  lines.push(`sizes ${s.mode === "PCT_OF_EQUITY" ? `${pct(s.value, { digits: 0 })} of equity` : s.mode === "PCT_OF_INITIAL" ? `${pct(s.value, { digits: 0 })} of starting capital` : s.mode === "FIXED_CASH" ? usd(s.value) : `${s.value} shares`}${s.max_notional_per_trade ? ` (max ${usd(s.max_notional_per_trade)})` : ""}`);
  const exits = dsl.exit?.any ?? [];
  lines.push(exits.length ? `exits on ${exits.map((e) => e.type === "RULE" ? `rule(${e.condition.feature})` : `${e.type.toLowerCase()} ${e.value}`).join(", ")}` : "holds to resolution");
  return lines;
}
