import { cn } from "@/lib/utils";

function tierColor(score: number): { fg: string; bg: string } {
  if (score >= 75) return { fg: "#00D68F", bg: "rgba(0,214,143,0.14)" };
  if (score >= 50) return { fg: "#F5A623", bg: "rgba(245,166,35,0.14)" };
  return { fg: "#FF4D4F", bg: "rgba(255,77,79,0.14)" };
}

export function ScoreBadge({ score, size = "md" }: { score: number; size?: "sm" | "md" }) {
  const { fg, bg } = tierColor(score);
  const width = size === "sm" ? 52 : 68;
  const height = size === "sm" ? 20 : 24;

  return (
    <div
      className="relative inline-flex items-center justify-center rounded-[4px] font-mono font-semibold tabular"
      style={{ width, height, background: bg, color: fg }}
      title={`Score: ${score.toFixed(0)} / 100`}
    >
      {/* fill gauge */}
      <div
        className="absolute left-0 top-0 h-full rounded-[4px] opacity-30"
        style={{ width: `${Math.min(100, Math.max(0, score))}%`, background: fg }}
      />
      <span className="relative z-10 text-[13px]">{score.toFixed(0)}</span>
    </div>
  );
}

export function RiskPill({ risk }: { risk: "low" | "medium" | "high" }) {
  const styles = {
    low: "text-gain border-gain/30 bg-gain/10",
    medium: "text-catalyst border-catalyst/30 bg-catalyst/10",
    high: "text-loss border-loss/30 bg-loss/10",
  } as const;
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide", styles[risk])}>
      {risk}
    </span>
  );
}
