"use client";

import { useEffect, useState } from "react";
import { getLatestScan, getProviderHealth, triggerScan } from "@/lib/api";
import type { ProviderHealth, ScanRun } from "@/types/stock";
import { ScannerTable } from "@/components/scanner-table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

const SCAN_TIMES = ["08:00", "08:30", "09:00", "09:20", "09:28"];

export default function Home() {
  const [scan, setScan] = useState<ScanRun | null>(null);
  const [health, setHealth] = useState<ProviderHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadLatest() {
    try {
      const [latest, providerHealth] = await Promise.all([getLatestScan(), getProviderHealth()]);
      setScan(latest);
      setHealth(providerHealth);
      setError(null);
    } catch {
      setError("No scan data yet. Trigger a scan below, or wait for the next scheduled run.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadLatest();
    const interval = setInterval(loadLatest, 60_000);
    return () => clearInterval(interval);
  }, []);

  async function handleRunScan() {
    setScanning(true);
    try {
      const run = await triggerScan();
      setScan(run);
      setError(null);
    } catch {
      setError("Scan failed to run — check the backend logs.");
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1800px] px-6 py-6">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-4 border-b border-border pb-5">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-text-primary">
            ORB<span className="text-gain">.</span>Scanner
          </h1>
          <p className="mt-1 text-sm text-text-muted">
            Pre-market momentum scanner — ORB, First Pullback, VWAP &amp; Momentum Breakout setups
          </p>
        </div>

        <div className="flex items-center gap-3">
          {health && (
            <Badge className={health.mode === "live" ? "border-gain/30 bg-gain/10 text-gain" : "border-catalyst/30 bg-catalyst/10 text-catalyst"}>
              {health.name} · {health.mode === "live" ? "Live" : "Demo data"}
            </Badge>
          )}
          <div className="hidden items-center gap-1 text-xs text-text-dim md:flex">
            <span>Scans:</span>
            {SCAN_TIMES.map((t) => (
              <span key={t} className="rounded bg-panel-raised px-1.5 py-0.5 font-mono">
                {t}
              </span>
            ))}
            <span>ET</span>
          </div>
          <Button onClick={handleRunScan} disabled={scanning}>
            {scanning ? "Scanning…" : "Run Scan Now"}
          </Button>
        </div>
      </header>

      {loading ? (
        <div className="py-24 text-center text-text-muted">Loading latest scan…</div>
      ) : (
        <>
          {scan && (
            <div className="mb-4 flex flex-wrap items-center gap-4 text-xs text-text-muted">
              <span>
                Last run: <span className="font-mono text-text-primary">{scan.scheduled_slot}</span>
              </span>
              <span>
                Scanned <span className="font-mono text-text-primary">{scan.candidates_scanned}</span> · Passed{" "}
                <span className="font-mono text-gain">{scan.candidates_passed}</span>
              </span>
              <span>
                Provider: <span className="font-mono text-text-primary">{scan.provider}</span>
              </span>
            </div>
          )}

          {error && (
            <div className="mb-4 rounded-md border border-catalyst/30 bg-catalyst/10 px-4 py-3 text-sm text-catalyst">
              {error}
            </div>
          )}

          <ScannerTable results={scan?.results ?? []} />
        </>
      )}
    </div>
  );
}
