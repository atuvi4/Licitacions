import { formatDate } from "@/lib/format";
import { CONFIDENCE_LABELS, type ExtractionConfidence } from "@/lib/requirements";

import { cn } from "./cn";

export type AttributionKind = "ai" | "rule" | "human";

export interface AttributionProps {
  kind: AttributionKind;
  /** Substitueix el text per defecte. */
  label?: string;
  person?: { name: string };
  /** ISO 8601. */
  at?: string;
  confidence?: ExtractionConfidence;
  className?: string;
}

function initials(name: string | undefined): string {
  if (!name) return "—";
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "");
  return letters.join("") || "—";
}

function defaultText({ kind, person, at, confidence }: AttributionProps): string {
  if (kind === "ai") {
    const trust = confidence ? ` · confiança ${CONFIDENCE_LABELS[confidence].toLowerCase()}` : "";
    return `Proposat per l'anàlisi${trust}`;
  }
  if (kind === "rule") return "Calculat amb el perfil";
  const parts = [person?.name ?? "Una persona"];
  if (at) parts.push(formatDate(at));
  return parts.join(" · ");
}

/**
 * Qui ho afirma (handoff §2.2). Sense color: és una firma. La confiança es diu
 * en text, no en color. Quan una persona contradiu el càlcul, la seva versió
 * mana i l'automàtica queda a sota, plegada, amb el motiu escrit.
 */
export function Attribution(props: AttributionProps) {
  const { kind, className } = props;
  const mark = kind === "ai" ? "IA" : kind === "rule" ? "ƒ" : initials(props.person?.name);
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[12.5px] text-muted", className)}>
      <i
        aria-hidden
        data-kind={kind}
        className={cn(
          "grid h-4 w-4 shrink-0 place-items-center border font-heading text-[8.5px] font-semibold not-italic leading-none",
          kind === "human" ? "border-brand-strong bg-brand-soft text-accent-800" : "border-border-strong text-muted",
        )}
      >
        {mark}
      </i>
      <span>{props.label ?? defaultText(props)}</span>
    </span>
  );
}
