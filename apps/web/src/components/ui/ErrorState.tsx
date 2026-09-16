import { CircleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";

export interface ErrorStateProps {
  title: string;
  description: string;
  action: ReactNode;
  secondary?: ReactNode;
  /** Plegat dins de «Detall tècnic». El codi no surt mai al títol ni a la descripció. */
  technical?: { code?: string | null; requestId?: string | null; at?: string; method?: string };
  className?: string;
}

/**
 * Frase, acció, detall plegat (handoff §2.8). Mateixa anatomia que EmptyState
 * amb la marca en Bloqueig. Inline, apareix on s'ha premut; a pàgina sencera,
 * només per a errors de càrrega.
 */
export function ErrorState({ title, description, action, secondary, technical, className }: ErrorStateProps) {
  const detail = technical
    ? [technical.method, technical.code, technical.requestId ? `req ${technical.requestId}` : null, technical.at]
        .filter((part): part is string => Boolean(part))
        .join(" · ")
    : "";
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-10 py-14 text-center", className)}>
      <span className="grid h-11 w-11 place-items-center border border-danger text-danger">
        <CircleAlert size={22} strokeWidth={1.5} aria-hidden />
      </span>
      <h3 className="font-heading text-[22px] leading-tight font-semibold text-ink">{title}</h3>
      <p className="max-w-[46ch] text-body leading-relaxed text-muted">{description}</p>
      <div className="mt-1 flex flex-wrap items-center justify-center gap-2.5">
        {action}
        {secondary}
      </div>
      {technical && detail && (
        <details className="mt-1 text-caption text-muted">
          <summary className="cursor-pointer">Detall tècnic</summary>
          {/* Única excepció a la regla de lint: el codi viu aquí, plegat, per a qui l'hagi de reportar. */}
          <p className="mt-1 font-mono text-[11.5px]">{detail}</p>
        </details>
      )}
    </div>
  );
}
