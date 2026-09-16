"use client";

import { Button } from "./Button";
import { cn } from "./cn";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-skeleton rounded-control bg-neutral-200", className)} />;
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-5">
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  );
}

/**
 * Avís d'error compacte dels consumidors anteriors al redisseny. Les pantalles
 * migrades fan servir ErrorState (frase, acció, detall plegat); aquest es
 * conserva perquè cap import es trenqui i es restilitza només amb els tokens.
 */
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-control border border-danger bg-danger-soft px-4 py-3">
      <p className="min-w-0 text-body break-words text-danger">{message}</p>
      {onRetry && (
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Torna-ho a provar
        </Button>
      )}
    </div>
  );
}

/**
 * Resultat d'una acció que ha anat bé.
 *
 * Existeix perquè una acció que retorna 200 i no canvia res a la pantalla és
 * indistingible d'una que ha fallat: qui l'ha premuda es queda sense saber si
 * el problema és seu o del sistema. Va passar amb «Reconcilia amb l'anàlisi»,
 * que era literalment el botó que un avís et demanava prémer.
 *
 * `tone="info"` per al cas neutre —s'ha fet i no hi havia res a fer— i
 * `tone="success"` quan alguna cosa ha canviat de veritat. Al Sprint 1 fa el
 * paper del Toast del handoff (§2.14).
 */
export function ResultNotice({
  message,
  detail,
  tone = "info",
  onDismiss,
}: {
  message: string;
  detail?: string;
  tone?: "info" | "success";
  onDismiss?: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 rounded-control border px-4 py-3",
        tone === "success" ? "border-success bg-success-soft" : "border-border-strong bg-subtle",
      )}
    >
      <div className="min-w-0">
        <p className={cn("text-body font-medium", tone === "success" ? "text-success" : "text-ink")}>{message}</p>
        {detail && <p className="mt-1 text-[13px] leading-5 text-muted">{detail}</p>}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="-my-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-lg text-muted transition-colors hover:bg-neutral-200 hover:text-ink"
          aria-label="Tanca l'avís"
        >
          ×
        </button>
      )}
    </div>
  );
}
