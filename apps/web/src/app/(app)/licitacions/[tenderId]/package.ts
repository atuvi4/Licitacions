import { formatDate } from "@/lib/format";
import type { Level } from "@/lib/labels";
import {
  DOCUMENT_ROLE_LABELS,
  type PackageGapCode,
  type PackageStatus,
  type TenderDocumentMeta,
  type TenderDocumentOrigin,
  type TenderPackageGap,
} from "@/lib/tenders";

/**
 * Plecs (3B-2 §7.1.5): d'on surt cada document, si l'anàlisi el pot llegir i què
 * falta al paquet. Tot surt del servidor (`origin`, `contribution_status`,
 * `duplicate_of_document_id`, `removed_at`, `analyzable`, `package`); aquí només
 * es tradueix. Cap codi intern arriba al text.
 */

export const DOCUMENT_ORIGIN_BADGES: Record<TenderDocumentOrigin, string> = {
  feed: "feed",
  operator_url: "aportat per l'operador",
  operator_upload: "pujat per l'operador",
  owner_upload: "pujat per l'empresa",
};

export function originBadge(document: TenderDocumentMeta): string {
  return DOCUMENT_ORIGIN_BADGES[document.origin] ?? "origen desconegut";
}

export interface Badge {
  label: string;
  level: Level;
}

/** L'estat d'una aportació de l'operador. `null` per al feed i les pujades pròpies. */
export function contributionBadge(document: TenderDocumentMeta): Badge | null {
  switch (document.contribution_status) {
    case "pending_verification":
      return { label: "pendent de verificació", level: "attention" };
    case "verified":
      return { label: "verificat", level: "ready" };
    case "rejected":
      return { label: "rebutjat", level: "blocking" };
    default:
      return null;
  }
}

/**
 * Qui ha retirat el document i des de quan (M3). Només un document governat pel
 * feed el retira la font; un d'aportat o pujat l'ha retirat l'operador (o
 * l'empresa, si era seu): atribuir-ho a la font seria una afirmació falsa.
 */
export function removedLabel(document: TenderDocumentMeta): string | null {
  if (!document.removed_at) return null;
  const since = `(des del ${formatDate(document.removed_at)})`;
  if (document.origin === "feed") return `Retirat de la font ${since}`;
  if (document.origin === "owner_upload") return `Retirat per l'empresa ${since}`;
  return `Retirat per l'operador ${since}`;
}

/** Les files de la taula: un àlies no en té de pròpia si el seu canònic hi és. */
export function topLevelDocuments(documents: TenderDocumentMeta[]): TenderDocumentMeta[] {
  const ids = new Set(documents.map((document) => document.id));
  return documents.filter(
    (document) => !document.duplicate_of_document_id || !ids.has(document.duplicate_of_document_id),
  );
}

/** Els àlies d'un document (mateixos bytes), per plegar-los a sota seu. */
export function aliasesOf(canonical: TenderDocumentMeta, documents: TenderDocumentMeta[]): TenderDocumentMeta[] {
  return documents.filter((document) => document.duplicate_of_document_id === canonical.id);
}

/** M = analitzables (ho diu el servidor); N = dels quals ja hi ha text llegit. */
export function readingCount(documents: TenderDocumentMeta[]): { read: number; total: number } {
  const analyzable = documents.filter((document) => document.analyzable);
  return {
    read: analyzable.filter((document) => document.latest_file?.analysis_status === "extracted").length,
    total: analyzable.length,
  };
}

export function readingLine(documents: TenderDocumentMeta[]): string {
  const { read, total } = readingCount(documents);
  if (total === 0) return "Cap document analitzable";
  return total === 1
    ? `${read} de 1 document analitzable llegit`
    : `${read} de ${total} documents analitzables llegits`;
}

export const PACKAGE_STATUS_LABELS: Record<PackageStatus, string> = {
  complete: "Paquet complet",
  incomplete: "Paquet incomplet",
  unknown: "Paquet sense comprovar",
};

/** Rols de document (`DocumentRole` del servidor): el diccionari viu a `lib/tenders`. */
export { DOCUMENT_ROLE_LABELS };

/** Seccions que un document que falta aportaria (`would_provide`). */
export const PACKAGE_SECTION_LABELS: Record<string, string> = {
  requirements: "requisits",
  award_criteria: "criteris d'adjudicació",
  scope_items: "abast",
  blocks: "blocs",
  sites: "seus",
  territories: "territoris",
  contract_facts: "fets del contracte",
};

function documentName(key: string | null, names: Map<string, string>): string {
  return (key && names.get(key)) || "Un document";
}

const GAP_TEXT: Record<PackageGapCode, (gap: TenderPackageGap, names: Map<string, string>) => string> = {
  required_role_missing: (gap) =>
    gap.key && Object.hasOwn(DOCUMENT_ROLE_LABELS, gap.key) && gap.key !== "other"
      ? `Falta el ${DOCUMENT_ROLE_LABELS[gap.key]}`
      : "Falta un document obligatori",
  referenced_document_missing: (gap) =>
    gap.key ? `Falta un document que cita el plec (${gap.key})` : "Falta un document que cita el plec",
  no_text: (gap, names) => `${documentName(gap.key, names)}: sense text llegible`,
  format_not_analyzable: (gap, names) => `${documentName(gap.key, names)}: format que no es pot llegir`,
  annex_missing: (gap) => (gap.key ? `Falta un annex (${gap.key})` : "Falta un annex"),
  declared_by_operator: () => "Falta un document (declarat per l'operador)",
};

/** Què falta, en humà. `names`: id de document → nom, per als forats d'un document. */
export function gapText(gap: TenderPackageGap, names: Map<string, string>): string {
  const render = Object.hasOwn(GAP_TEXT, gap.code) ? GAP_TEXT[gap.code] : null;
  return render ? render(gap, names) : "Falta part del paquet documental";
}

/** Què aportaria el que falta; buit vol dir que no se sap. */
export function gapImpact(gap: TenderPackageGap): string {
  if (gap.would_provide.length === 0) return "impacte desconegut";
  const labels = [
    ...new Set(gap.would_provide.map((section) => PACKAGE_SECTION_LABELS[section] ?? "altres dades")),
  ];
  return `aportaria: ${labels.join(", ")}`;
}
