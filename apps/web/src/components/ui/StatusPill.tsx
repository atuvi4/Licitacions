import type { ReactNode } from "react";

import { cn } from "./cn";

/**
 * L'escala única de senyal (handoff §2.1): cinc nivells amb significat fix més
 * `action` per a la selecció. El nivell surt sempre d'un mapa de `lib/labels.ts`,
 * mai d'un `if` al JSX.
 */
export type Level = "neutral" | "running" | "ready" | "attention" | "blocking" | "action";

const FILL: Record<Level, string> = {
  neutral: "bg-neutral-200 text-muted",
  running: "bg-running-soft text-running",
  ready: "bg-success-soft text-success",
  attention: "bg-warning-soft text-warning",
  blocking: "bg-danger-soft text-danger",
  action: "bg-brand text-white",
};

const OUTLINE: Record<Level, string> = {
  neutral: "border-border-strong text-muted",
  running: "border-running text-running",
  ready: "border-success text-success",
  attention: "border-warning text-warning",
  blocking: "border-danger text-danger",
  action: "border-brand text-brand-strong",
};

export interface StatusPillProps {
  level: Level;
  children: ReactNode;
  size?: "sm" | "md";
  /** Només la vora: per als filtres de recompte. */
  outline?: boolean;
  className?: string;
}

export function StatusPill({ level, children, size = "md", outline = false, className }: StatusPillProps) {
  return (
    <span
      data-level={level}
      data-outline={outline ? "true" : undefined}
      className={cn(
        "inline-flex items-center gap-1.5 font-medium whitespace-nowrap tabular-nums",
        size === "sm" ? "px-[7px] py-px text-[11.5px] leading-[1.5]" : "px-[9px] py-[2px] text-[12.5px] leading-[1.5]",
        outline ? cn("border bg-transparent", OUTLINE[level]) : FILL[level],
        className,
      )}
    >
      {children}
    </span>
  );
}
