"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { addToWatchlist, getStockDetail } from "@/lib/api";
import type { StockDetail } from "@/types/stock";
import { formatCompact, formatCurrency, formatPct } from "@/lib/utils";
import { ScoreBadge, RiskPill } from "@/components/score-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const BREAKDOWN_LABELS: Record<string, string> = {
  gap: "Gap %",
  float: "Float",
  relative_volume: "Relative Volume",
  premarket_volume: "Premarket Volume",
  news_quality: "News Quality",
  atr: "ATR",
  average_volume: "Average Volume",
  spread: "Spread",
  previous_resistance: "Prev. Resistance",
  historical_volatility: "Historical Volatility",
  recent_halts: "Recent Halts",
};

export default function StockDetailPage() {
  const params = useParams<{ ticker: string }>();
  const [stock, setStock] = useState<StockDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [watchlisted, setWatchlisted] = useState(false);

  useEffect(() => {
    getStockDetail(params.ticker)
      .then(setStock)
      .catch(() => setError(`No scan data found for ${params.ticker.toUpperCase()}.`));
  }, [params.ticker]);

  async function handleWatchlist() {
    if (!stock) return;
    try {
      await addToWatchlist(stock.ticker);
      setWatchlisted(true);
    } catch {
      // already on the watchlist, or the request failed — either way, no crash
      setWatchlisted(true);
    }
  }

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] px-6 py-16 text-center">
        <p className="text-text-primary">{error}</p>
        <Link href="/" className="mt-4 inline-block text-sm text-gain hover:underline">
          ← Back to scanner
        </Link>
      </div>
    );
  }

  if (!stock) {
    return <div className="py-24 text-center text-text-muted">Loading {params.ticker.toUpperCase()}…</div>;
  }

  return (
    <div className="mx-auto max-w-[1200px] px-6 py-6">
      <Link href="/" className="text-sm text-text-muted hover:text-gain">
        ← Back to scanner
      </Link>

      <header className="mt-3 mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-border pb-5">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-display text-3xl font-bold text-text-primary">{stock.ticker}</h1>
            <ScoreBadge score={stock.score} />
            <RiskPill risk={stock.risk} />
          </div>
          <p className="mt-1 text-text-muted">
            {stock.company} · {stock.sector}
          </p>
        </div>
        <div className="text-right">
          <div className="font-mono text-2xl font-semibold tabular">{formatCurrency(stock.price)}</div>
          <div className={`font-mono text-sm tabular ${stock.gap_pct >= 0 ? "text-gain" : "text-loss"}`}>
            {formatPct(stock.gap_pct)} gap
          </div>
          <Button variant="ghost" className="mt-2" onClick={handleWatchlist} disabled={watchlisted}>
            {watchlisted ? "★ On Watchlist" : "☆ Add to Watchlist"}
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Trading plan */}
        <section className="rounded-lg border border-border bg-panel p-5 lg:col-span-2">
          <h2 className="font-display text-sm font-semibold uppercase tracking-wide text-text-muted">Trading Plan</h2>
          <p className="mt-1 text-xs text-text-dim">
            Derived from the premarket range — a starting point for your own plan, not a recommendation.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <PlanStat label="ORB Entry" value={formatCurrency(stock.trading_plan.orb_entry)} accent="info" />
            <PlanStat label="Pullback Entry" value={formatCurrency(stock.trading_plan.pullback_entry)} accent="info" />
            <PlanStat label="Stop" value={formatCurrency(stock.trading_plan.stop)} accent="loss" />
            <PlanStat label="Target" value={formatCurrency(stock.trading_plan.target)} accent="gain" />
          </div>
          <div className="mt-4 text-sm text-text-muted">
            Risk/Reward:{" "}
            <span className="font-mono font-semibold text-text-primary">{stock.trading_plan.risk_reward_ratio.toFixed(2)}R</span>
          </div>

          {/* Premarket range visual */}
          <div className="mt-6">
            <div className="mb-1.5 flex justify-between text-xs text-text-dim">
              <span>PM Low {formatCurrency(stock.premarket_low)}</span>
              <span>PM High {formatCurrency(stock.premarket_high)}</span>
            </div>
            <RangeBar low={stock.premarket_low} high={stock.premarket_high} price={stock.price} />
          </div>

          <div className="mt-6 rounded-md border border-dashed border-border p-4 text-xs text-text-dim">
            Intraday &amp; premarket candlestick charts (TradingView Lightweight Charts) render here once the{" "}
            <code className="rounded bg-panel-raised px-1 py-0.5 text-text-muted">/candles</code> endpoint is wired to the
            provider&apos;s bar data — next milestone on this build.
          </div>
        </section>

        {/* Catalyst / News */}
        <section className="rounded-lg border border-border bg-panel p-5">
          <h2 className="font-display text-sm font-semibold uppercase tracking-wide text-text-muted">Catalyst</h2>
          {stock.has_catalyst ? (
            <>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {stock.catalyst_tags.map((tag) => (
                  <Badge key={tag} className="border-catalyst/30 bg-catalyst/10 text-catalyst">
                    {tag}
                  </Badge>
                ))}
              </div>
              {stock.news_headline && (
                <p className="mt-3 text-sm text-text-primary">
                  {stock.news_url ? (
                    <a href={stock.news_url} target="_blank" rel="noreferrer" className="hover:text-gain hover:underline">
                      {stock.news_headline}
                    </a>
                  ) : (
                    stock.news_headline
                  )}
                </p>
              )}
            </>
          ) : (
            <p className="mt-3 text-sm text-text-dim">No catalyst detected in recent news.</p>
          )}
        </section>

        {/* Key stats */}
        <section className="rounded-lg border border-border bg-panel p-5 lg:col-span-2">
          <h2 className="font-display text-sm font-semibold uppercase tracking-wide text-text-muted">Key Stats</h2>
          <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <Stat label="Float" value={formatCompact(stock.float_shares)} />
            <Stat label="Market Cap" value={formatCompact(stock.market_cap)} />
            <Stat label="Relative Volume" value={`${stock.relative_volume.toFixed(1)}x`} />
            <Stat label="Premarket Volume" value={formatCompact(stock.premarket_volume)} />
            <Stat label="Average Volume" value={formatCompact(stock.average_volume)} />
            <Stat label="ATR" value={stock.atr.toFixed(2)} />
            <Stat label="Expected Volatility" value={`${stock.expected_volatility_pct.toFixed(1)}%`} />
            <Stat label="Support" value={formatCurrency(stock.support)} />
            <Stat label="Resistance" value={formatCurrency(stock.resistance)} />
            <Stat label="Prev. Day High" value={formatCurrency(stock.previous_day_high)} />
            <Stat label="Prev. Day Low" value={formatCurrency(stock.previous_day_low)} />
            <Stat
              label="Short Interest"
              value={stock.short_interest_pct ? `${stock.short_interest_pct.toFixed(1)}%` : "N/A"}
            />
          </div>
        </section>

        {/* Score breakdown */}
        <section className="rounded-lg border border-border bg-panel p-5">
          <h2 className="font-display text-sm font-semibold uppercase tracking-wide text-text-muted">Score Breakdown</h2>
          <div className="mt-4 space-y-3">
            {Object.entries(stock.score_breakdown).map(([key, value]) => (
              <div key={key}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="text-text-muted">{BREAKDOWN_LABELS[key] ?? key}</span>
                  <span className="font-mono text-text-primary">{value.toFixed(0)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-panel-raised">
                  <div className="h-full rounded-full bg-gain" style={{ width: `${Math.min(100, value)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function PlanStat({ label, value, accent }: { label: string; value: string; accent: "info" | "loss" | "gain" }) {
  const color = { info: "text-info", loss: "text-loss", gain: "text-gain" }[accent];
  return (
    <div>
      <div className="text-xs text-text-dim">{label}</div>
      <div className={`font-mono text-lg font-semibold tabular ${color}`}>{value}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-text-dim">{label}</div>
      <div className="font-mono text-sm tabular text-text-primary">{value}</div>
    </div>
  );
}

function RangeBar({ low, high, price }: { low: number; high: number; price: number }) {
  const span = Math.max(high - low, 0.01);
  const pct = Math.min(100, Math.max(0, ((price - low) / span) * 100));
  return (
    <div className="relative h-2 rounded-full bg-panel-raised">
      <div className="absolute h-full rounded-full bg-gradient-to-r from-loss via-catalyst to-gain opacity-40" style={{ width: "100%" }} />
      <div
        className="absolute top-1/2 h-3 w-3 -translate-y-1/2 -translate-x-1/2 rounded-full border-2 border-bg bg-text-primary"
        style={{ left: `${pct}%` }}
        title={`Current: ${formatCurrency(price)}`}
      />
    </div>
  );
}
