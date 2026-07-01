import type { ProviderHealth, ScanRun, StockDetail } from "@/types/stock";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function getLatestScan(): Promise<ScanRun> {
  return apiFetch<ScanRun>("/scans/latest");
}

export function getStockDetail(ticker: string): Promise<StockDetail> {
  return apiFetch<StockDetail>(`/stocks/${ticker}`);
}

export function triggerScan(): Promise<ScanRun> {
  return apiFetch<ScanRun>("/scans/run", { method: "POST" });
}

export async function getProviderHealth(): Promise<ProviderHealth | null> {
  try {
    const res = await apiFetch<{ status: string; provider: ProviderHealth }>("/health");
    return res.provider;
  } catch {
    return null;
  }
}

export function addToWatchlist(ticker: string, note = ""): Promise<unknown> {
  return apiFetch("/watchlist", { method: "POST", body: JSON.stringify({ ticker, note }) });
}
