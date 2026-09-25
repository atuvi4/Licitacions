import type { ReactNode } from "react";

import { cn } from "./cn";

/**
 * Tres nivells i cap més (handoff §2.6): `panel` (per defecte), `frame`
 * (objecte de decisió, amb una vora una mica més marcada) i, per a llistes
 * clau-valor, un panell sense capçalera. Targeta blanca, vora subtil, ombra mínima.
 */
export function Card({
  className,
  children,
  frame = false,
  id,
}: {
  className?: string;
  children: ReactNode;
  /** Vora més marcada. Només decisió, aprovació i benvinguda. */
  frame?: boolean;
  /** Àncora per a enllaços interns («#tasques»). */
  id?: string;
}) {
  return (
    <section
      id={id}
      data-frame={frame ? "true" : undefined}
      className={cn(
        "relative rounded-card border bg-surface shadow-card",
        frame ? "border-border-strong" : "border-border",
        className,
      )}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-t-card border-b border-border px-5 py-3.5">
      <div className="min-w-0">
        <h2 className="font-heading text-[17px] leading-tight font-semibold text-ink">{title}</h2>
        {description && <p className="mt-1 text-[13px] leading-5 text-muted">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        <h1 className="font-heading text-page font-semibold text-ink">{title}</h1>
        {description && <p className="mt-1.5 text-body leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
