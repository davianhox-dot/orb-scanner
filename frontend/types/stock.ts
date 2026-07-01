export interface ScanResult {
  id: string;
  ticker: string;
  company: string;
  sector: string;
  price: number;
  gap_pct: number;
  premarket_pct: number;
  premarket_volume: number;
  relative_volume: number;
  float_shares: number;
  market_cap: number;
  has_catalyst: boolean;
  catalyst_tags: string[];
  news_headline: string;
  news_url: string;
  score: number;
  score_breakdown: Record<string, number>;
  risk: "low" | "medium" | "high";
  premarket_high: number;
  premarket_low: number;
  support: number;
  resistance: number;
  average_volume: number;
  atr: number;
  expected_volatility_pct: number;
  recent_halt: boolean;
  short_interest_pct: number;
  previous_day_high: number;
  previous_day_low: number;
  created_at: string;
}

export interface TradingPlan {
  orb_entry: number;
  pullback_entry: number;
  stop: number;
  target: number;
  risk_reward_ratio: number;
}

export interface StockDetail extends ScanResult {
  trading_plan: TradingPlan;
}

export interface ScanRun {
  id: string;
  scheduled_slot: string;
  started_at: string;
  finished_at: string | null;
  provider: string;
  candidates_scanned: number;
  candidates_passed: number;
  status: "running" | "success" | "error";
  error_message: string | null;
  results: ScanResult[];
}

export interface ProviderHealth {
  name: string;
  configured: boolean;
  mode: "live" | "mock";
  detail: string;
}
