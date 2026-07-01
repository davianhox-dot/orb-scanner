import { cn } from "@/lib/utils";

export function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-border bg-panel-raised px-2 py-0.5 text-[11px] font-medium text-text-muted",
        className
      )}
    >
      {children}
    </span>
  );
}
