import Link from "next/link";

import { formatDate } from "@/lib/format";

import { cn } from "./cn";
import { StatusPill } from "./StatusPill";

export interface EvidenceRefProps {
  documentName: string | null;
  pageNumber: number | null;
  extractionRevision?: number | null;
  /**
   * Exactament el que ja construeix `EvidenceLink`:
   * `/licitacions/{tenderId}/documents/{documentId}?page={n}&extraction={id}`.
   * Apunta a la revisió citada, no a la vigent.
   */
  href: string | null;
  quote?: string | null;
  extractedAt?: string | null;
  /** Només la línia «document · pàgina · revisió». */
  compact?: boolean;
  className?: string;
}

/**
 * Cita literal + document + pàgina + revisió d'extracció (handoff §2.3).
 * Sense cita ho diu en Atenció: el requisit mai s'oculta.
 */
export function EvidenceRef({
  documentName,
  pageNumber,
  extractionRevision,
  href,
  quote,
  extractedAt,
  compact = false,
  className,
}: EvidenceRefProps) {
  if (!documentName && pageNumber === null) {
    return (
      <StatusPill level="attention" size="sm" className={className}>
        Sense cita al plec
      </StatusPill>
    );
  }

  const parts = [documentName ?? "Document"];
  if (pageNumber !== null) parts.push(`p. ${pageNumber}`);
  if (extractionRevision !== null && extractionRevision !== undefined) parts.push(`revisió ${extractionRevision}`);
  if (extractedAt) parts.push(`llegit el ${formatDate(extractedAt)}`);
  const line = parts.join(" · ");
  const linkLabel = pageNumber !== null ? `Obre la pàgina ${pageNumber} del plec →` : "Obre el plec →";

  if (compact) {
    return (
      <span className={cn("inline-flex flex-wrap items-baseline gap-x-2 text-caption", className)}>
        <span className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">{line}</span>
        {href && (
          <Link href={href} className="inline-block py-1 text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800">
            {linkLabel}
          </Link>
        )}
      </span>
    );
  }

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">{line}</p>
      {quote && (
        <blockquote className="border-l-2 border-accent-500 pl-2.5 text-[13px] leading-relaxed text-ink italic">
          «{quote}»
        </blockquote>
      )}
      {href && (
        <p className="text-[13px]">
          <Link href={href} className="inline-block py-1 text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800">
            {linkLabel}
          </Link>
        </p>
      )}
    </div>
  );
}
