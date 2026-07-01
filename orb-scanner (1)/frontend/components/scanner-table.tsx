"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { ScanResult } from "@/types/stock";
import { formatCompact, formatCurrency, formatPct } from "@/lib/utils";
import { ScoreBadge, RiskPill } from "@/components/score-badge";
import { Badge } from "@/components/ui/badge";

type SortKey = keyof Pick<
  ScanResult,
  | "ticker"
  | "price"
  | "gap_pct"
  | "premarket_pct"
  | "premarket_volume"
  | "relative_volume"
  | "float_shares"
  | "market_cap"
  | "score"
  | "premarket_high"
  | "premarket_low"
  | "support"
  | "resistance"
  | "average_volume"
  | "atr"
  | "expected_volatility_pct"
>;

const COLUMNS: { key: SortKey | "company" | "sector" | "news" | "risk"; label: string; align?: "right" }[] = [
  { key: "ticker", label: "Ticker" },
  { key: "company", label: "Company" },
  { key: "sector", label: "Sector" },
  { key: "price", label: "Price", align: "right" },
  { key: "gap_pct", label: "Gap %", align: "right" },
  { key: "premarket_pct", label: "PM %", align: "right" },
  { key: "premarket_volume", label: "PM Vol", align: "right" },
  { key: "relative_volume", label: "Rel Vol", align: "right" },
  { key: "float_shares", label: "Float", align: "right" },
  { key: "market_cap", label: "Mkt Cap", align: "right" },
  { key: "news", label: "News" },
  { key: "score", label: "Score", align: "right" },
  { key: "risk", label: "Risk" },
  { key: "premarket_high", label: "PM High", align: "right" },
  { key: "premarket_low", label: "PM Low", align: "right" },
  { key: "support", label: "Support", align: "right" },
  { key: "resistance", label: "Resistance", align: "right" },
  { key: "average_volume", label: "Avg Vol", align: "right" },
  { key: "atr", label: "ATR", align: "right" },
  { key: "expected_volatility_pct", label: "Exp Vol", align: "right" },
];

export function ScannerTable({ results }: { results: ScanResult[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const sorted = useMemo(() => {
    const copy = [...results];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" && typeof bv === "string") {
        return sortDir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      const an = Number(av) || 0;
      const bn = Number(bv) || 0;
      return sortDir === "asc" ? an - bn : bn - an;
    });
    return copy;
  }, [results, sortKey, sortDir]);

  function handleSort(key: string) {
    const validKey = key as SortKey;
    if (!(validKey in (results[0] ?? {}))) return;
    if (sortKey === validKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(validKey);
      setSortDir("desc");
    }
  }

  if (results.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-24 text-center">
        <p className="font-display text-lg text-text-primary">No candidates passed the filters this scan.</p>
        <p className="text-sm text-text-muted">
          That&apos;s expected outside pre-market hours, or if the current filters are strict. Adjust thresholds in Settings.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[1600px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-panel-raised text-left text-[11px] uppercase tracking-wide text-text-muted">
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                onClick={() => handleSort(col.key)}
                className={`sticky top-0 select-none whitespace-nowrap px-3 py-2.5 font-medium ${
                  col.align === "right" ? "text-right" : "text-left"
                } ${col.key !== "company" && col.key !== "sector" && col.key !== "news" && col.key !== "risk" ? "cursor-pointer hover:text-text-primary" : ""} ${
                  col.key === "ticker" ? "sticky left-0 z-10 bg-panel-raised" : ""
                }`}
              >
                {col.label}
                {sortKey === col.key && <span className="ml-1 text-gain">{sortDir === "asc" ? "↑" : "↓"}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id} className="shadow-row hover:bg-panel-raised/60 transition-colors">
              <td className="sticky left-0 z-10 whitespace-nowrap bg-bg px-3 py-2.5 font-mono font-semibold">
                <Link href={`/stock/${r.ticker}`} className="text-text-primary hover:text-gain">
                  {r.ticker}
                </Link>
              </td>
              <td className="max-w-[180px] truncate px-3 py-2.5 text-text-muted">{r.company}</td>
              <td className="whitespace-nowrap px-3 py-2.5 text-text-dim">{r.sector}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular">{formatCurrency(r.price)}</td>
              <td className={`px-3 py-2.5 text-right font-mono tabular ${r.gap_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {formatPct(r.gap_pct)}
              </td>
              <td className={`px-3 py-2.5 text-right font-mono tabular ${r.premarket_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {formatPct(r.premarket_pct)}
              </td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCompact(r.premarket_volume)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular">{r.relative_volume.toFixed(1)}x</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCompact(r.float_shares)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCompact(r.market_cap)}</td>
              <td className="px-3 py-2.5">
                {r.has_catalyst ? (
                  <div className="flex flex-wrap gap-1">
                    {r.catalyst_tags.slice(0, 2).map((tag) => (
                      <Badge key={tag} className="border-catalyst/30 bg-catalyst/10 text-catalyst">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <span className="text-text-dim">None</span>
                )}
              </td>
              <td className="px-3 py-2.5 text-right">
                <ScoreBadge score={r.score} size="sm" />
              </td>
              <td className="px-3 py-2.5">
                <RiskPill risk={r.risk} />
              </td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCurrency(r.premarket_high)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCurrency(r.premarket_low)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCurrency(r.support)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCurrency(r.resistance)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{formatCompact(r.average_volume)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{r.atr.toFixed(2)}</td>
              <td className="px-3 py-2.5 text-right font-mono tabular text-text-muted">{r.expected_volatility_pct.toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
