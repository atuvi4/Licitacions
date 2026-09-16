import type { ReactNode } from "react";

import { cn } from "./cn";

export interface PermissionNoteProps {
  /** El botó, ja desactivat al 45 %. Es veu; no s'amaga. */
  children: ReactNode;
  /** «Només {who} pot {what}.» */
  who: string;
  what: string;
  /** Un camí per demanar-ho («Demana-ho a…»). */
  action?: ReactNode;
  className?: string;
}

/** Un permís denegat s'explica (handoff §2.9): qui sí que ho pot fer i com demanar-ho. */
export function PermissionNote({ children, who, what, action, className }: PermissionNoteProps) {
  return (
    <div className={cn("inline-flex flex-col items-start gap-1.5", className)}>
      {children}
      <p className="text-[13px] leading-5 text-warning">
        Només {who} pot {what}.
      </p>
      {action && <div className="text-[13px]">{action}</div>}
    </div>
  );
}
