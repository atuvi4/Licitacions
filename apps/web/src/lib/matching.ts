/**
 * Pre-fit: encaix entre el perfil de l'empresa i les metadades d'una licitació.
 *
 * A diferència de `tenders.ts`, això SÍ que són dades del tenant.
 *
 * Terminologia deliberada, i cal mantenir-la: això és un **pre-fit** calculat
 * només amb metadades públiques (CPV, territori, import, terminis). No és una
 * probabilitat de guanyar ni un Go/No-Go — encara no s'ha llegit ni una línia
 * dels plecs. Els textos d'aquest mòdul són el que veurà l'usuari, i han de
 * dir exactament això.
 */

import type { TenderListItem } from "@/lib/tenders";

/** Avís que ha d'acompanyar sempre un pre-fit a la interfície. */
export const PREFIT_DISCLAIMER =
  "Aquest pre-fit utilitza només metadades públiques. Els requisits dels plecs encara no s'han analitzat.";

export type MatchStatus =
  | "candidate"
  | "low_fit"
  | "excluded"
  | "closed"
  | "insufficient_data";

export type ExclusionKind = "business" | "status";

export type DimensionName = "cpv" | "geography" | "economic" | "timing";

export interface MatchFactor {
  dimension: DimensionName;
  result: "match" | "partial" | "no_match" | "unknown";
  reason_code: string;
  weight: number;
  score: number | null;
  max_score: number;
  company_value: string | null;
  source_value: string | null;
  detail: Record<string, unknown>;
}

/** El que comparteixen el pre-fit d'un expedient i el d'un lot. */
export interface MatchCommon {
  id: string;
  score: number | null;
  status: MatchStatus;
  exclusion_kind: ExclusionKind | null;
  exclusion_reason_code: string | null;
  cpv_score: number | null;
  geographic_score: number | null;
  economic_score: number | null;
  timing_score: number | null;
  data_coverage: number;
  factors: MatchFactor[];
  rules_version: string;
  calculated_at: string;
}

export interface LotMatch extends MatchCommon {
  tender_lot_id: string;
  lot_number: number | null;
  lot_title: string | null;
}

export interface TenderMatch extends MatchCommon {
  tender_id: string;
  best_lot_id: string | null;
  candidate_lot_count: number;
  total_lot_count: number;
  is_stale: boolean;
  lot_matches: LotMatch[];
}

export interface TenderMatchListItem {
  tender: TenderListItem;
  match: TenderMatch | null;
}

export interface MatchSummary {
  total_tenders: number;
  /** Licitacions visibles amb pre-fit calculat. 0 = el Radar encara no s'ha calculat. */
  evaluated_count: number;
  actionable: number;
  candidates: number;
  high_fit: number;
  low_fit: number;
  excluded: number;
  closed: number;
  insufficient_data: number;
  stale: number;
  rules_version: string;
  last_calculated_at: string | null;
}

export interface RecalculationResult {
  tenders_evaluated: number;
  lots_evaluated: number;
  created: number;
  updated: number;
  duration_ms: number;
  rules_version: string;
}

/* -------------------------------------------------------------------------- */
/* Etiquetes                                                                   */
/* -------------------------------------------------------------------------- */

export const MATCH_STATUS_LABELS: Record<MatchStatus, string> = {
  candidate: "Candidata",
  low_fit: "Encaix baix",
  excluded: "Descartada",
  closed: "Fora de termini",
  insufficient_data: "Dades insuficients",
};

export const MATCH_STATUS_TONE: Record<
  MatchStatus,
  "success" | "warning" | "danger" | "brand" | "neutral"
> = {
  candidate: "success",
  low_fit: "neutral",
  excluded: "danger",
  closed: "neutral",
  insufficient_data: "warning",
};

/** Què vol dir cada estat, en una frase. Es mostra com a ajuda contextual. */
export const MATCH_STATUS_HELP: Record<MatchStatus, string> = {
  candidate:
    "Encaixa amb l'activitat, el territori i la capacitat declarades. Val la pena mirar-se els plecs.",
  low_fit: "Les metadades no mostren prou encaix amb el que fa l'empresa.",
  excluded: "Queda fora per una preferència que heu configurat vosaltres, no per cap norma legal.",
  closed: "El termini ha passat o l'expedient ja no admet ofertes.",
  insufficient_data:
    "La font no publica prou metadades per valorar-la. No vol dir que no encaixi.",
};

export const DIMENSION_LABELS: Record<DimensionName, string> = {
  cpv: "Activitat (CPV)",
  geography: "Territori",
  economic: "Import",
  timing: "Termini",
};

export const DIMENSION_ORDER: DimensionName[] = ["cpv", "geography", "economic", "timing"];

/**
 * Motius, en llenguatge planer.
 *
 * Cada codi que retorna el motor ha de tenir aquí una frase que un humà entengui:
 * el valor del pre-fit és que es pugui explicar, no el número.
 */
export const REASON_LABELS: Record<string, string> = {
  // CPV
  CPV_EXACT_HIGH_PRIORITY: "El CPV coincideix exactament amb una activitat prioritària",
  CPV_EXACT_MEDIUM_PRIORITY: "El CPV coincideix exactament amb una activitat declarada",
  CPV_EXACT_LOW_PRIORITY: "El CPV coincideix exactament amb una activitat secundària",
  CPV_RELATED_CATEGORY: "CPV de la mateixa categoria que una activitat declarada",
  CPV_RELATED_CLASS: "CPV de la mateixa classe que una activitat declarada",
  CPV_RELATED_GROUP: "CPV del mateix grup que una activitat declarada",
  CPV_RELATED_DIVISION: "CPV de la mateixa divisió, però d'una àrea diferent",
  CPV_NO_OVERLAP: "Cap CPV en comú amb les activitats declarades",
  CPV_INACTIVE_ONLY: "Només coincideix amb un CPV que teniu desactivat",
  CPV_EXCLUDED: "Coincideix amb un CPV que heu marcat com a exclòs",
  CPV_UNKNOWN: "La licitació no publica cap codi CPV",
  CPV_NO_COMPANY_PREFERENCES: "L'empresa encara no ha declarat cap CPV",

  // Territori
  TERRITORY_HIGH_PRIORITY: "És en un territori prioritari",
  TERRITORY_MEDIUM_PRIORITY: "És en un territori declarat",
  TERRITORY_ALLOWED: "És en un territori on accepteu treballar",
  TERRITORY_NOT_DECLARED: "No és en cap dels territoris que heu declarat",
  TERRITORY_EXCLUDED: "És en un territori on heu indicat que no voleu treballar",
  TERRITORY_UNKNOWN: "La licitació no publica on s'executa",
  TERRITORY_NO_COMPANY_DATA: "L'empresa encara no ha declarat cap territori",

  // Import
  AMOUNT_PREFERRED: "L'import és dins la franja preferida",
  AMOUNT_INTERESTING: "L'import supera el mínim que us interessa",
  AMOUNT_ABOVE_PREFERRED: "L'import supera la franja preferida",
  AMOUNT_ABOVE_RECOMMENDED: "L'import supera el màxim recomanat que heu configurat",
  AMOUNT_ABOVE_MAXIMUM: "L'import supera el màxim assumible que heu configurat",
  AMOUNT_BELOW_MINIMUM: "L'import és per sota del mínim que us interessa",
  AMOUNT_UNKNOWN: "La licitació no publica l'import",
  AMOUNT_NO_COMPANY_PREFERENCES: "L'empresa encara no ha configurat la capacitat de contractació",

  // Termini
  DEADLINE_COMFORTABLE: "Queda temps de sobres per preparar l'oferta",
  DEADLINE_ADEQUATE: "El termini és suficient",
  DEADLINE_SHORT: "El termini és curt",
  DEADLINE_CRITICAL: "El termini és molt ajustat",
  DEADLINE_EXPIRED: "El termini de presentació ja ha passat",
  DEADLINE_UNKNOWN: "La licitació no publica el termini de presentació",
  STATUS_NOT_OPEN: "L'expedient ja no admet ofertes",
};

export function reasonLabel(code: string | null): string {
  if (!code) return "—";
  return REASON_LABELS[code] ?? code;
}

/** Un pre-fit amb l'activitat sense avaluar no s'ha de llegir com un mal encaix. */
export function isUnknown(factor: MatchFactor): boolean {
  return factor.result === "unknown";
}

/** Color del número segons el llindar de candidatura (60) i el d'encaix alt (80). */
export function scoreTone(score: number | null): "success" | "warning" | "neutral" {
  if (score === null) return "neutral";
  if (score >= 80) return "success";
  if (score >= 60) return "warning";
  return "neutral";
}

/** Frase curta per a la fila del feed: el motiu que més pesa. */
export function shortSummary(match: MatchCommon): string {
  if (match.status === "closed" || match.status === "excluded") {
    return reasonLabel(match.exclusion_reason_code);
  }
  if (match.status === "insufficient_data") {
    return "No hi ha prou metadades publicades per valorar-la";
  }
  const cpv = match.factors.find((factor) => factor.dimension === "cpv");
  const weakest = [...match.factors]
    .filter((factor) => !isUnknown(factor))
    .sort((a, b) => (a.score ?? 0) / a.max_score - (b.score ?? 0) / b.max_score)[0];

  if (match.status === "low_fit" && cpv) {
    return reasonLabel(cpv.reason_code);
  }
  return weakest ? reasonLabel(weakest.reason_code) : "—";
}

/** "Millor lot: 91 · 2 de 3 lots candidats" */
export function lotSummary(match: TenderMatch): string | null {
  if (match.total_lot_count === 0) return null;
  const best = match.lot_matches[0];
  const parts: string[] = [];
  if (best && best.score !== null) parts.push(`Millor lot: ${best.score}`);
  parts.push(
    `${match.candidate_lot_count} de ${match.total_lot_count} ${
      match.total_lot_count === 1 ? "lot candidat" : "lots candidats"
    }`,
  );
  return parts.join(" · ");
}

/** El que el perfil ha de tenir perquè el pre-fit es pugui calcular (3B-2 §7.1.4). */
export interface PrefitProfileCounts {
  cpv_preferences: number;
  territories: number;
  contract_preferences: number;
}

/**
 * Si el perfil té CPV, territoris o preferències de contracte: sense cap d'aquests,
 * el pre-fit no es calcula (ni a la ingesta ni a demanda). `null` = encara no se sap.
 */
export function prefitProfileReady(counts: PrefitProfileCounts | null | undefined): boolean | null {
  if (!counts) return null;
  return counts.cpv_preferences + counts.territories + (counts.contract_preferences ?? 0) > 0;
}

/** Per què el Radar té 0 candidates (revisió 3B-2, F1). */
export type CandidatesCause = "not_evaluated" | "profile" | "insufficient_data" | "no_match";

/**
 * La causa d'un 0 candidates, UNA sola funció per a Avui i per a Oportunitats ›
 * Radar. `null` si no hi ha cap 0 a explicar o el resum del Radar no ha arribat.
 *
 * - Cap licitació avaluada → `not_evaluated`, sigui com sigui el perfil.
 * - Avaluades i el perfil SENSE CPV, territoris NI preferències de contracte
 *   (`prefitProfileReady === false`) → `profile`: és cert i és la raó. Amb
 *   territoris però sense CPV, la causa del perfil no està demostrada.
 * - Totes les avaluades sense dades suficients → `insufficient_data`.
 * - Si no → `no_match`.
 */
export function candidatesCause(
  radar: Pick<MatchSummary, "evaluated_count" | "candidates" | "insufficient_data"> | null | undefined,
  profile?: PrefitProfileCounts | null,
): CandidatesCause | null {
  if (!radar || radar.candidates > 0) return null;
  if (radar.evaluated_count === 0) return "not_evaluated";
  if (prefitProfileReady(profile) === false) return "profile";
  if (radar.insufficient_data === radar.evaluated_count) return "insufficient_data";
  return "no_match";
}
