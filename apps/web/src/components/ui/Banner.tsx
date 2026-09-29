import type { ReactNode } from "react";

import { cn } from "./cn";

export type BannerLevel = "neutral" | "attention" | "blocking" | "ready";

// Fons tou i vora a to amb poca opacitat: l'avís es llegeix pel rètol, no pel pes.
const LEVEL_CLASS: Record<BannerLevel, { box: string; kicker: string }> = {
  neutral: { box: "border-border bg-subtle", kicker: "text-muted" },
  attention: { box: "border-warning/25 bg-warning-soft", kicker: "text-warning" },
  blocking: { box: "border-danger/25 bg-danger-soft", kicker: "text-danger" },
  ready: { box: "border-success/25 bg-success-soft", kicker: "text-success" },
};

/**
 * Un sol bloc d'avisos per pantalla, ordenat per gravetat (handoff §2.4).
 * Cap Banner sense acció, excepte `ready`: el tipus ho imposa.
 */
export type BannerProps = {
  kicker: string;
  children: ReactNode;
  className?: string;
} & ({ level: "ready"; action?: ReactNode } | { level: Exclude<BannerLevel, "ready">; action: ReactNode });

export function Banner({ level, kicker, children, action, className }: BannerProps) {
  const classes = LEVEL_CLASS[level];
  return (
    <div
      role="status"
      data-level={level}
      className={cn(
        "flex flex-col gap-2 rounded-card border px-4 py-3 sm:flex-row sm:items-center sm:gap-4",
        classes.box,
        className,
      )}
    >
      <span className={cn("shrink-0 font-heading text-[13px] font-semibold tracking-[0.06em] uppercase", classes.kicker)}>
        {kicker}
      </span>
      <div className="min-w-0 flex-1 text-body text-ink">{children}</div>
      {action && <div className="shrink-0 sm:ml-auto">{action}</div>}
    </div>
  );
}
