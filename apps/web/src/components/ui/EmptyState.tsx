import type { ReactNode } from "react";

import { cn } from "./cn";

export interface EmptyStateProps {
  title: string;
  description?: string;
  /** Obligatòria: cap buit sense sortida. Si l'acció no és del rol, és un PermissionNote. */
  action: ReactNode;
  secondary?: ReactNode;
  /** Icona Lucide de 22 px; es dibuixa dins d'un quadrat hairline de 44 px. */
  icon?: ReactNode;
  className?: string;
}

/**
 * Cap buit sense sortida (handoff §2.7). Explica què passa, què no s'ha perdut
 * i quina és la sortida; mai un dibuix simpàtic sense acció ni un «0» que
 * sembli una avaluació.
 */
export function EmptyState({ title, description, action, secondary, icon, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-10 py-14 text-center", className)}>
      {icon && (
        <span className="grid h-11 w-11 place-items-center border border-border-strong text-brand-strong [&>svg]:h-[22px] [&>svg]:w-[22px]">
          {icon}
        </span>
      )}
      <h3 className="font-heading text-[22px] leading-tight font-semibold text-ink">{title}</h3>
      {description && <p className="max-w-[46ch] text-body leading-relaxed text-muted">{description}</p>}
      <div className="mt-1 flex flex-wrap items-center justify-center gap-2.5">
        {action}
        {secondary}
      </div>
    </div>
  );
}
