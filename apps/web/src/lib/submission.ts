/**
 * El manifest congelat d'una revisió: el contracte d'S7 i com es llegeix.
 *
 * El manifest **no es construeix aquí**. El servidor el congela i el serveix
 * byte a byte, perquè el hash que hi ha a l'aprovació és el d'aquells bytes;
 * aquest fitxer només el sap llegir i traduir-lo a paraules. No s'hi recalcula
 * cap hash, cap empremta i cap import.
 *
 * I el que no hi és, no s'hi posa: S7 deixa fora el cost, el marge, el markup,
 * les notes internes i els pressupostos. Una vista «enriquida» al client seria
 * exactament el que el manifest evita.
 */

export const MANIFEST_VERSION_V2 = "submission-manifest-v2";
export const MANIFEST_VERSION_V1 = "submission-manifest-v1";

/** Els imports que es presentarien. `null` és indeterminat, mai zero. */
export interface ManifestAmounts {
  revenue: string | null;
  vat_amount: string | null;
  total_with_vat: string | null;
}

export interface ManifestDocument {
  position: number;
  document_id: string | null;
  filename: string;
  sha256: string | null;
  file_size: number | null;
  mime_type: string | null;
  category: string | null;
  requirement_id: string | null;
  requirement_title: string | null;
  expires_at: string | null;
}

export interface ManifestSolution {
  solution_id: string;
  requirement_id: string | null;
  title: string | null;
  proposed_solution: string | null;
  supplier_name: string | null;
  product_name: string | null;
}

export interface ManifestOfferLine {
  line_id: string;
  position: number;
  concept: string;
  quantity: string | null;
  pricing_basis: string | null;
  duration_months_override: number | null;
  effective_duration_months: number | null;
  periods: string | null;
  offered_unit_price_ex_vat: string | null;
  vat_rate: string | null;
  supplier_name: string | null;
  product_name: string | null;
  requirement_id: string | null;
  technical_solution_id: string | null;
  amounts: ManifestAmounts;
}

export interface ManifestOfferSection {
  section_id: string;
  title: string;
  position: number;
  lines: ManifestOfferLine[];
  amounts: ManifestAmounts;
}

export interface ManifestOfferLot {
  lot_id: string;
  tender_lot_id: string | null;
  label: string | null;
  position: number;
  duration_months: number | null;
  sections: ManifestOfferSection[];
  amounts: ManifestAmounts;
}

export interface ManifestOffer {
  currency: string | null;
  lots: ManifestOfferLot[];
  amounts: ManifestAmounts;
}

export interface ManifestValidation {
  solution_id: string;
  source: string | null;
  requirement_id: string | null;
  status: string | null;
  validated_requirement_fingerprint: string | null;
  validated_content_fingerprint: string | null;
}

export interface ManifestRelevance {
  evidence_item_id: string;
  [key: string]: unknown;
}

export interface Manifest {
  manifest_version?: string;
  revision_number?: number;
  workspace_id?: string;
  company_id?: string;
  tender?: {
    tender_id?: string;
    tender_lot_id?: string | null;
    expediente_number?: string | null;
    deadline_at?: string | null;
  };
  approval_basis?: {
    analysis?: { requirement_run_id?: string | null; compliance_run_id?: string | null; prefit_score?: number | null };
    dossier_relevance?: ManifestRelevance[];
    technical_scope_fingerprint?: string | null;
    technical_validations?: ManifestValidation[];
  };
  content?: {
    coverage?: { mandatory_total?: number; mandatory_covered?: number; document_count?: number };
    documents?: ManifestDocument[];
    technical?: ManifestSolution[];
    economic?: ManifestOffer | null;
  };
}

/* ------------------------------------------------------------------------- */
/* Versió del manifest                                                        */
/* ------------------------------------------------------------------------- */

export type ManifestFormat = "current" | "legacy" | "unknown";

/**
 * Quin format té aquesta revisió.
 *
 * Importa per un motiu concret (C-17): un manifest d'un format anterior no
 * descrivia ni la solució tècnica ni l'oferta, i per això el servidor no en
 * compara el hash —serien dues serialitzacions diferents— sinó que mira si algú
 * el va aprovar.
 */
export function manifestFormat(version: string | null | undefined): ManifestFormat {
  if (version === MANIFEST_VERSION_V2) return "current";
  if (version === MANIFEST_VERSION_V1) return "legacy";
  return "unknown";
}

export const MANIFEST_FORMAT_LABELS: Record<ManifestFormat, string> = {
  current: "Format actual",
  legacy: "Format anterior",
  unknown: "Format desconegut",
};

/** Cert quan el manifest porta el contingut tècnic i econòmic (v2). */
export function hasFullContent(manifest: Manifest | null): boolean {
  return manifestFormat(manifest?.manifest_version) === "current";
}

/* ------------------------------------------------------------------------- */
/* Lectura segura                                                             */
/* ------------------------------------------------------------------------- */

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * El manifest tal com arriba, sense donar per fet cap camp.
 *
 * Un manifest d'una versió que aquesta interfície no conegui no ha de petar ni
 * ha de fer-se passar per un de complet: es llegeix el que es pugui i la resta
 * queda al detall tècnic.
 */
export function readManifest(payload: unknown): Manifest | null {
  return isRecord(payload) ? (payload as Manifest) : null;
}

export function manifestDocuments(manifest: Manifest | null): ManifestDocument[] {
  const rows = manifest?.content?.documents;
  return Array.isArray(rows) ? rows : [];
}

export function manifestSolutions(manifest: Manifest | null): ManifestSolution[] {
  const rows = manifest?.content?.technical;
  return Array.isArray(rows) ? rows : [];
}

export function manifestOffer(manifest: Manifest | null): ManifestOffer | null {
  const offer = manifest?.content?.economic;
  return isRecord(offer) ? (offer as ManifestOffer) : null;
}

export function manifestValidations(manifest: Manifest | null): ManifestValidation[] {
  const rows = manifest?.approval_basis?.technical_validations;
  return Array.isArray(rows) ? rows : [];
}

export function manifestRelevance(manifest: Manifest | null): ManifestRelevance[] {
  const rows = manifest?.approval_basis?.dossier_relevance;
  return Array.isArray(rows) ? rows : [];
}

/** Totes les línies de l'oferta congelada, en ordre de lot i secció. */
export function manifestLines(offer: ManifestOffer | null): Array<{ lot: ManifestOfferLot; section: ManifestOfferSection; line: ManifestOfferLine }> {
  if (!offer || !Array.isArray(offer.lots)) return [];
  const rows: Array<{ lot: ManifestOfferLot; section: ManifestOfferSection; line: ManifestOfferLine }> = [];
  for (const lot of offer.lots) {
    for (const section of lot.sections ?? []) {
      for (const line of section.lines ?? []) rows.push({ lot, section, line });
    }
  }
  return rows;
}

/**
 * Els camps que el manifest NO pot portar mai (S7).
 *
 * Existeix per poder-ho comprovar en una prova i perquè quedi escrit: si algun
 * dia un d'aquests noms apareix al snapshot, és un problema del servidor i no
 * una cosa que la interfície hagi de pintar.
 */
export const FORBIDDEN_MANIFEST_FIELDS = [
  "unit_cost_ex_vat",
  "total_cost",
  "margin_amount",
  "margin_on_sales_pct",
  "markup_on_cost_pct",
  "monthly_equivalent_cost",
  "internal_economics",
  "notes",
  "tender_max_unit_price_ex_vat",
  "exceeds_unit_max",
  "exceeds_lot_max",
  "revision",
] as const;

/* ------------------------------------------------------------------------- */
/* Etiquetes                                                                  */
/* ------------------------------------------------------------------------- */

export const PRICING_BASIS_LABELS: Record<string, string> = {
  one_off: "Preu tancat",
  monthly: "Mensual",
  annual: "Anual",
  per_unit: "Per unitat",
};

export function pricingBasisLabel(basis: string | null): string {
  if (!basis) return "—";
  return PRICING_BASIS_LABELS[basis] ?? basis;
}

export const APPROVAL_DECISION_LABELS: Record<string, string> = {
  approved_for_submission: "Aprovada",
  rejected: "Rebutjada",
};

export function approvalDecisionLabel(decision: string): string {
  return APPROVAL_DECISION_LABELS[decision] ?? decision;
}
