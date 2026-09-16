import type { ReactNode } from "react";

import { cn } from "./cn";
import { RegistrationMarks } from "./Marks";

/**
 * Tres nivells i cap més (handoff §2.6): `panel` (per defecte), `frame`
 * (objecte de decisió amb marques de registre) i, per a llistes clau-valor,
 * un panell sense capçalera. Sense ombra, sense radi, fons transparent.
 */
export function Card({
  className,
  children,
  frame = false,
}: {
  className?: string;
  children: ReactNode;
  /** Vora neutral-500 + marques de registre. Només decisió, aprovació i benvinguda. */
  frame?: boolean;
}) {
  return (
    <section
      className={cn("relative rounded-card border bg-transparent", frame ? "border-border-strong" : "border-border", className)}
    >
      {frame && <RegistrationMarks size="md" tone="accent" />}
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
    <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border bg-subtle px-3.5 py-2.5">
      <div className="min-w-0">
        <h2 className="font-heading text-[17px] leading-tight font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] leading-5 text-muted">{description}</p>}
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
    <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        <h1 className="font-heading text-page font-semibold text-ink">{title}</h1>
        {description && <p className="mt-1.5 text-body leading-6 text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
