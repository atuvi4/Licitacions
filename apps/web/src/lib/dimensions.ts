import type { SourceRefCitation } from "@/lib/requirements";
import type { Level } from "@/components/ui";

/**
 * Dimensionament: les magnituds de què depèn la candidatura.
 *
 * Tot el mòdul descansa en una distinció: una **observació** diu què deia una
 * font, i una **decisió** diu què farà servir la candidatura. La primera la pot
 * registrar qualsevol; la segona porta un nom i un motiu al costat.
 *
 * El que aquí no hi ha, i és deliberat: cap manera d'editar o esborrar una
 * observació, i cap camp per posar l'estat a mà. Les dues coses existirien si
 * «decidit» fos només un valor més.
 */

export type DimensionStatus = "undecided" | "decided" | "disputed";
export type ObservationSourceKind = "tender_document" | "supplier" | "internal" | "other";

export interface DimensionObservation {
  id: string;
  source_kind: ObservationSourceKind;
  value_text: string;
  value_num: string | null;
  note: string | null;
  created_at: string;
  created_by: string | null;
  source_ref: SourceRefCitation | null;
}

export interface Dimension {
  id: string;
  key: string;
  label: string;
  unit: string | null;
  scope_label: string | null;
  working_value_text: string | null;
  working_value_num: string | null;
  status: DimensionStatus;
  decision_reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  archived_at: string | null;
  /** El testimoni de CAS: el següent escrit l'ha de portar. */
  revision: number;
  observations: DimensionObservation[];
}

export const STATUS_LABELS: Record<DimensionStatus, string> = {
  undecided: "Sense decidir",
  decided: "Decidida",
  disputed: "Fonts que no coincideixen",
};

/**
 * Una discrepància avisa; no bloqueja.
 *
 * Posar-la en vermell diria que el sistema sap que hi ha un error, i no ho sap:
 * sap que dues fonts no diuen el mateix, que és una cosa que passa sovint i que
 * resol una persona.
 */
export const STATUS_LEVEL: Record<DimensionStatus, Level> = {
  undecided: "neutral",
  decided: "ready",
  disputed: "attention",
};

export const SOURCE_KIND_LABELS: Record<ObservationSourceKind, string> = {
  tender_document: "Plec",
  supplier: "Proveïdor",
  internal: "Intern",
  other: "Altres",
};

export const SOURCE_KINDS: ObservationSourceKind[] = [
  "tender_document",
  "supplier",
  "internal",
  "other",
];

/** Qui pot dimensionar. Els mateixos que editen les solucions tècniques. */
export function whoCanEditDimensions(): string {
  return "els rols Propietari, Administrador, Responsable d'oferta i Tècnic";
}

export function canEditDimensions(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "bid_manager" || role === "technical";
}

/**
 * Com es llegeix una magnitud: el valor decidit si n'hi ha, i si no, res.
 *
 * Deliberadament NO cau cap a «la primera observació» quan encara no s'ha
 * decidit. Ensenyar-ne una com si fos el valor de treball és exactament el que
 * passa avui amb un full de càlcul: algú la copia i ja ningú no recorda que
 * ningú no l'havia triada.
 */
export function workingValue(dimension: Dimension): string | null {
  if (dimension.working_value_text === null) return null;
  return dimension.unit
    ? `${dimension.working_value_text} ${dimension.unit}`
    : dimension.working_value_text;
}

/** Els números diferents que afirmen les fonts, en l'ordre en què van arribar. */
export function distinctValues(dimension: Dimension): string[] {
  const seen: string[] = [];
  for (const observation of dimension.observations) {
    if (observation.value_num === null) continue;
    const normalised = String(Number(observation.value_num));
    if (!seen.includes(normalised)) seen.push(normalised);
  }
  return seen;
}

/** Una frase que diu què passa, sense fer-ne una alarma. */
export function statusExplanation(dimension: Dimension): string {
  if (dimension.status === "disputed") {
    const values = distinctValues(dimension);
    return values.length >= 2
      ? `Les fonts diuen ${values.join(" i ")}. Cal triar el valor de treball i dir per què.`
      : "Les fonts no coincideixen. Cal triar el valor de treball i dir per què.";
  }
  if (dimension.status === "decided") return dimension.decision_reason ?? "";
  return dimension.observations.length === 0
    ? "Encara no hi ha cap font."
    : "Cap contradicció. Es pot fixar el valor de treball quan convingui.";
}
