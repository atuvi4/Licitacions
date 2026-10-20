/**
 * Preparació final: el contracte d'S6 i com es diu cada cosa en pantalla.
 *
 * El veredicte NO es calcula aquí. El backend diu si la candidatura està a punt
 * i per què no ho està; la interfície només tradueix codis a frases i porta a
 * l'acció que els resol. Si algun dia divergissin, tindríem dues portes que
 * diuen coses diferents, que és exactament el que S6 va venir a impedir.
 *
 * Un codi que aquesta versió no conegui **no s'amaga**: es mostra amb el codi
 * cru i la severitat que diu el servidor. Amagar-lo seria dir que no hi és.
 */

export type FindingSeverity = "blocker" | "warning" | "info";
export type FindingDomain = "workspace" | "dossier" | "technical" | "economic";

export interface ReadinessFinding {
  code: string;
  severity: FindingSeverity;
  domain: FindingDomain;
  entity_id: string | null;
  context: Record<string, unknown>;
}

export interface DossierReadiness {
  state: string;
  blockers: string[];
}

export interface TechnicalReadiness {
  scope_reviewed: boolean;
  scope_stale: boolean;
  scope_reviewed_at: string | null;
  scope_reviewed_by: string | null;
  requirements_in_scope: number;
  findings: ReadinessFinding[];
}

export interface EconomicReadiness {
  offer_exists: boolean;
  findings: ReadinessFinding[];
}

export interface DocumentReviewState {
  reviewed: boolean;
  stale: boolean;
  reviewed_at: string | null;
  documents: number;
}

export interface FinalReadiness {
  workspace_id: string;
  /** La licitació: els plecs no es resolen dins de Preparar. */
  tender_id: string;
  ready_for_final_review: boolean;
  findings: ReadinessFinding[];
  dossier: DossierReadiness;
  documents: DocumentReviewState;
  technical: TechnicalReadiness;
  economic: EconomicReadiness;
}

/**
 * `GET /companies/{id}/workspaces/preparation-counts` (P2-09): els blocadors de
 * `final-readiness` de cada candidatura oberta i dins de termini, d'una sola crida.
 * `pending` nul = no s'ha pogut comptar, mai zero.
 */
export interface PreparationCounts {
  items: { workspace_id: string; pending: number | null }[];
}

/* ------------------------------------------------------------------------- */
/* Àrees de Preparar                                                          */
/* ------------------------------------------------------------------------- */

export type PrepareArea = "solucio" | "oferta" | "dossier" | "pla";

/** Quatre àrees i cap més: la vista de resum es va descartar per a la V1. */
export const PREPARE_AREAS = ["solucio", "oferta", "dossier", "pla"] as const;

export const PREPARE_AREA_LABELS: Record<PrepareArea, string> = {
  solucio: "Solució tècnica",
  oferta: "Oferta econòmica",
  dossier: "Dossier",
  pla: "Pla de treball",
};

export function isPrepareArea(value: string | null | undefined): value is PrepareArea {
  return value !== null && value !== undefined && (PREPARE_AREAS as readonly string[]).includes(value);
}

/**
 * On es resol un problema. Pot ser una àrea de Preparar o un altre pas de la
 * candidatura: `decision_not_go` no es resol preparant res, es resol decidint.
 */
export type FindingDestination =
  | { kind: "area"; area: PrepareArea }
  | { kind: "step"; step: "decidir" }
  /**
   * Ni els plecs ni l'anàlisi són àrees de Preparar: viuen a la fitxa de la
   * licitació, que és on es poden mirar i tornar a executar.
   */
  | { kind: "tender"; tab: "plecs" | "requisits" };

export interface FindingCopy {
  label: string;
  /** Una línia de context quan el codi sol no s'explica. */
  hint?: string;
  /** Text de l'enllaç que hi porta. */
  action?: string;
  destination?: FindingDestination;
}

const area = (value: PrepareArea): FindingDestination => ({ kind: "area", area: value });
const DECIDE: FindingDestination = { kind: "step", step: "decidir" };
const PLECS: FindingDestination = { kind: "tender", tab: "plecs" };
const ANALISI: FindingDestination = { kind: "tender", tab: "requisits" };

/**
 * Codi del servidor → què llegeix una persona.
 *
 * Els codis són els que emet S6 de debò (`app/workspace/readiness.py` i els
 * blocadors del dossier a `app/workspace/dossier.py`). Cap frase afirma res que
 * el servidor no digui, i cap porta xifres internes: la preparació final no
 * parla mai de cost ni de marge.
 */
export const FINDING_COPY: Record<string, FindingCopy> = {
  /* --- Candidatura ------------------------------------------------------- */
  decision_not_go: {
    label: "Encara no hi ha un GO",
    hint: "Ningú no ha decidit que ens hi presentem.",
    action: "Ves a Decidir",
    destination: DECIDE,
  },
  // 3B-2 §7.1.3: sense anàlisi, zero requisits no vol dir que el plec no en tingui.
  analysis_missing: {
    label: "Encara no hi ha cap anàlisi del plec",
    hint: "Sense anàlisi no se sap quins requisits obligatoris té el plec.",
    action: "Analitza el plec",
    destination: ANALISI,
  },
  analysis_outdated: {
    label: "El compliment anterior ha quedat endarrerit",
    hint: "El plec o el compliment anterior han canviat des de l'última execució del motor anterior.",
    action: "Obre els requisits",
    destination: ANALISI,
  },

  /* --- Plecs -------------------------------------------------------------- */
  //
  // Cap d'aquestes tres frases diu que els requisits hagin canviat. Les dues
  // primeres parlen del que hi ha per llegir; la tercera, de fins on arriba
  // l'anàlisi. Fondre-les tornaria a ajuntar el que S3-S3 va separar.
  tender_documents_unreviewed: {
    label: "Ningú no ha revisat els plecs",
    hint: "Que no se n'hagi dit res no vol dir que hi hagi res malament.",
    action: "Obre els plecs",
    destination: PLECS,
  },
  tender_documents_changed: {
    label: "Hi ha canvis als plecs des de l'última revisió",
    hint: "Han arribat documents o versions noves.",
    action: "Obre els plecs",
    destination: PLECS,
  },
  requirement_analysis_predates_documents: {
    label: "Els requisits extrets no cobreixen els plecs d'ara",
    hint: "Els requisits vigents es van extreure d'uns altres documents.",
    // Es resol on viuen els requisits extrets (motor anterior), que és on es
    // poden tornar a extreure. Portar a Decidir seria enviar algú a un lloc on
    // no hi ha el botó que necessita.
    action: "Obre els requisits",
    destination: ANALISI,
  },

  /* --- Dossier ----------------------------------------------------------- */
  mandatory_unresolved: {
    label: "Hi ha requisits obligatoris sense resoldre",
    action: "Obre el dossier",
    destination: area("dossier"),
  },
  mandatory_checklist_blocked: {
    label: "Hi ha feina marcada com a bloquejada",
    action: "Obre el pla de treball",
    destination: area("pla"),
  },
  mandatory_evidence_missing: {
    label: "Falten documents obligatoris",
    action: "Obre el dossier",
    destination: area("dossier"),
  },
  mandatory_document_expired: {
    label: "Algun document obligatori no serveix per al termini",
    action: "Obre el dossier",
    destination: area("dossier"),
  },
  mandatory_tasks_open: {
    label: "Queden tasques obligatòries obertes",
    action: "Obre el pla de treball",
    destination: area("pla"),
  },
  mandatory_relevance_unproven: {
    label: "Hi ha papers triats sense confirmar que acrediten el que es demana",
    action: "Obre el dossier",
    destination: area("dossier"),
  },
  evidence_needs_review: {
    label: "Algú ha demanat revisar una evidència",
    action: "Obre el dossier",
    destination: area("dossier"),
  },

  /* --- Solució tècnica --------------------------------------------------- */
  technical_scope_unreviewed: {
    label: "Ningú no ha revisat l'abast tècnic",
    hint: "Cal dir quins requisits necessiten una solució tècnica.",
    action: "Obre la solució tècnica",
    destination: area("solucio"),
  },
  technical_scope_stale: {
    label: "L'abast tècnic ha canviat des de l'última revisió",
    hint: "Els requisits, els lots o les solucions triades ja no són els mateixos.",
    action: "Torna a revisar l'abast",
    destination: area("solucio"),
  },
  technical_solution_unvalidated: {
    label: "Hi ha una solució tècnica sense validar",
    action: "Obre la solució",
    destination: area("solucio"),
  },
  technical_solution_validation_stale: {
    label: "Una validació ha deixat de valer",
    hint: "El contingut o el requisit han canviat després de signar-la.",
    action: "Obre la solució",
    destination: area("solucio"),
  },
  technical_solution_requirement_missing: {
    label: "Una solució apunta a un requisit que ja no hi és",
    action: "Obre la solució",
    destination: area("solucio"),
  },

  /* --- Oferta econòmica -------------------------------------------------- */
  economic_offer_not_started: {
    label: "Encara no hi ha oferta econòmica",
    action: "Obre l'oferta",
    destination: area("oferta"),
  },
  economic_bid_lots_not_selected: {
    label: "No s'ha dit a quins lots ens presentem",
    action: "Tria els lots",
    destination: area("oferta"),
  },
  economic_lot_empty: {
    label: "Hi ha un lot sense cap línia",
    action: "Obre el lot",
    destination: area("oferta"),
  },
  economic_offer_price_missing: {
    label: "Hi ha una línia sense preu ofert",
    action: "Obre la línia",
    destination: area("oferta"),
  },
  economic_duration_missing: {
    label: "Una línia periòdica no diu quant dura",
    hint: "Sense durada, el seu total no es pot calcular.",
    action: "Obre la línia",
    destination: area("oferta"),
  },
  economic_unit_limit_exceeded: {
    label: "Un preu unitari supera el màxim del plec",
    action: "Obre la línia",
    destination: area("oferta"),
  },
  economic_lot_budget_exceeded: {
    label: "L'oferta d'un lot supera el pressupost del plec",
    action: "Obre el lot",
    destination: area("oferta"),
  },
  economic_vat_missing: {
    label: "Hi ha una línia amb l'IVA sense definir",
    action: "Obre la línia",
    destination: area("oferta"),
  },
  economic_unit_limit_unknown: {
    label: "No consta el màxim unitari del plec",
    hint: "No es pot dir si el preu hi cap.",
    destination: area("oferta"),
  },
  economic_lot_budget_unknown: {
    label: "No consta el pressupost del lot",
    hint: "No es pot dir si l'oferta hi cap.",
    destination: area("oferta"),
  },
};

/** Text per a un codi que aquesta versió no coneix. No s'amaga ni es dona per bo. */
export const UNKNOWN_FINDING_LABEL = "Aquesta versió de la interfície encara no reconeix aquest estat";

export function findingCopy(code: string): FindingCopy | null {
  return FINDING_COPY[code] ?? null;
}

/* ------------------------------------------------------------------------- */
/* Agrupació                                                                  */
/* ------------------------------------------------------------------------- */

export const DOMAIN_LABELS: Record<FindingDomain, string> = {
  workspace: "Candidatura",
  dossier: "Dossier",
  technical: "Solució tècnica",
  economic: "Oferta econòmica",
};

/**
 * Ordre fix dels dominis DINS de cada grup de gravetat. No es reordena segons
 * qui té més problemes: si l'ordre depengués del contingut, resoldre una cosa
 * en mouria una altra de lloc i qui llegeix la llista perdria el fil enmig de
 * la feina. Segueix l'ordre de les àrees de Preparar, amb la candidatura al
 * final perquè el que hi surt no es resol preparant res.
 */
const DOMAIN_ORDER: FindingDomain[] = ["dossier", "technical", "economic", "workspace"];

/** Els tres nivells del calaix, tal com els anomena el Design Freeze. */
export const SEVERITY_GROUP_LABELS: Record<FindingSeverity, string> = {
  blocker: "Cal resoldre per congelar",
  warning: "Avisos",
  info: "Per informació",
};

const SEVERITY_GROUPS: FindingSeverity[] = ["blocker", "warning", "info"];

export interface FindingGroup {
  severity: FindingSeverity;
  label: string;
  findings: ReadinessFinding[];
}

/** Només els bloquejos compten al capçal: un avís no impedeix res. */
export function blockerCount(findings: ReadinessFinding[]): number {
  return findings.filter((finding) => finding.severity === "blocker").length;
}

/** Etiqueta del comptador únic de pendents: compta les quatre àrees de Preparar (B6). */
export const BLOCKER_COUNT_SCOPE = "(totes les àrees)";

/** «N pendents de preparació (totes les àrees)»: el mateix text al capçal i a Tancar. */
export function blockerCountLabel(count: number): string {
  return `${count} ${count === 1 ? "pendent" : "pendents"} de preparació ${BLOCKER_COUNT_SCOPE}`;
}

/** El recompte d'una pestanya de Preparar dit amb paraules (P2-02): «1 pendent», «3 pendents». */
export function pendingCountLabel(count: number): string {
  return count === 1 ? "1 pendent" : `${count} pendents`;
}

/**
 * Bloquejos per àrea de Preparar: els que es resolen en aquella àrea. Un bloqueig que
 * es resol fora de Preparar (Decidir, la fitxa de la licitació) o amb un codi que no
 * coneixem compta al total però a cap àrea.
 */
export function blockersByArea(findings: ReadinessFinding[]): Record<PrepareArea, number> {
  const counts: Record<PrepareArea, number> = { solucio: 0, oferta: 0, dossier: 0, pla: 0 };
  for (const finding of findings) {
    if (finding.severity !== "blocker") continue;
    const destination = findingCopy(finding.code)?.destination;
    if (destination?.kind === "area") counts[destination.area] += 1;
  }
  return counts;
}

/**
 * Primer la gravetat, després el domini.
 *
 * Qui obre el calaix vol saber què l'impedeix presentar-se, no quina àrea té
 * més feina: per això manen els tres nivells i el domini viaja dins de cada
 * fila. Els nivells buits no es mostren, i els que hi són mantenen sempre el
 * mateix ordre intern.
 */
export function groupFindings(findings: ReadinessFinding[]): FindingGroup[] {
  return SEVERITY_GROUPS.map((severity) => {
    const own = findings
      .filter((finding) => finding.severity === severity)
      .slice()
      .sort((a, b) => DOMAIN_ORDER.indexOf(a.domain) - DOMAIN_ORDER.indexOf(b.domain));
    return { severity, label: SEVERITY_GROUP_LABELS[severity], findings: own };
  }).filter((group) => group.findings.length > 0);
}
