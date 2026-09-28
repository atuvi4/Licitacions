import type {
  AwardCriteriaView,
  AwardItemRef,
  CanonicalAwardCriterion,
  CanonicalRequirement,
  ReviewOverview,
} from "@/lib/review";

/**
 * Respostes de la revisió canònica amb la forma de Palamós (2024-3137):
 * 257 requisits (56 per revisar), 9 criteris que puntuen i 100 punts. Els
 * requisits són una mostra representativa amb els casos d'acceptació (M15,
 * M06, M21, M09), no els 257.
 */

const PPT = "PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf";
const PCAP = "PCAP. PLEC DE CLÀUSULES ADMINISTRATIVES PARTICULARS.pdf";
const INFORME = "Informe necessitats contractació.pdf";

export const reviewOverview: ReviewOverview = {
  tender_id: "t1",
  review_started: true,
  total_requirements: 257,
  pending_materialisation: 0,
  needs_review_count: 56,
  verification_counts: [
    { code: "unreviewed", label: "Pendent de revisar", count: 201 },
    { code: "verified", label: "Verificat", count: 0 },
    { code: "partial", label: "Parcial", count: 0 },
    { code: "rejected", label: "Descartat", count: 0 },
    { code: "needs_review", label: "Cal revisar", count: 56 },
  ],
  provenance_counts: [
    { code: "automated", label: "Extret automàticament", count: 214 },
    { code: "manual", label: "Afegit manualment", count: 43 },
  ],
};

function requirement(overrides: Partial<CanonicalRequirement> & Pick<CanonicalRequirement, "canonical_id" | "title">): CanonicalRequirement {
  return {
    description: null,
    category: "technical_requirement",
    category_label: "Requisit tècnic",
    obligation_label: "Obligatori",
    provenance: "automated",
    provenance_label: "Extret automàticament",
    verification_status: "unreviewed",
    verification_label: "Pendent de revisar",
    needs_review: false,
    values: [],
    evidence_label: "Al document, sense valors",
    evidence: [],
    documents: [PPT],
    pages: [],
    sections: [],
    review_revision: 1,
    visible_relations: [],
    folded_relations: [],
    ...overrides,
  };
}

const manual = {
  provenance: "manual",
  provenance_label: "Afegit manualment",
  evidence_label: "Citat manualment",
};

function manualCitation(page: number, section: string, quote: string) {
  return { document: PPT, page, section, quote, status_label: "Citat manualment" };
}

export const CLOUD_HA = "r-cloud";
export const SIP_DDI = "r-ddi";

export const reviewRequirements: CanonicalRequirement[] = [
  requirement({
    canonical_id: "m15",
    title: "Mínim 64 trucades simultànies",
    ...manual,
    values: [
      {
        key: "trucades_simultanies",
        operator: ">=",
        value: 64,
        unit_label: "trucades",
        source_label: "Afirmat pel revisor",
        status_label: "Afirmat pel revisor",
      },
    ],
    evidence: [manualCitation(23, "8.1.1.7", "mínim de 64 trucades simultànies")],
    pages: [23],
  }),
  requirement({
    canonical_id: "m06",
    title: "Solució cloud d'alta disponibilitat",
    ...manual,
    evidence: [manualCitation(22, "8.1.1.1", "solució cloud en alta disponibilitat")],
    pages: [22],
    visible_relations: [
      {
        relation_id: "rel-m06",
        direction: "outgoing",
        relation_type: "partial_match_of",
        relation_label: "Cobert parcialment per",
        other_item_id: CLOUD_HA,
        other_item_title: "Plataforma de comunicacions de veu fixa en alta disponibilitat al núvol",
        note: "Coincidencia parcial amb l'element automatic (annex M06).",
        foldable: false,
        created_at: "2026-09-27T20:24:09Z",
      },
    ],
  }),
  requirement({
    canonical_id: CLOUD_HA,
    title: "Plataforma de comunicacions de veu fixa en alta disponibilitat al núvol",
    evidence: [{ document: PPT, page: 32, section: null, quote: "plataforma al núvol en alta disponibilitat", status_label: "Al document, sense valors" }],
    pages: [32],
    visible_relations: [
      {
        relation_id: "rel-m06",
        direction: "incoming",
        relation_type: "partial_match_of",
        relation_label: "Cobreix parcialment",
        other_item_id: "m06",
        other_item_title: "Solució cloud d'alta disponibilitat",
        note: "Coincidencia parcial amb l'element automatic (annex M06).",
        foldable: false,
        created_at: "2026-09-27T20:24:09Z",
      },
    ],
  }),
  requirement({
    canonical_id: "m21",
    title: "Accés directe a extensió (DDI)",
    ...manual,
    evidence: [manualCitation(24, "8.1.1.9", "accés directe a extensió")],
    pages: [24],
    visible_relations: [
      {
        relation_id: "rel-m21",
        direction: "outgoing",
        relation_type: "related_to",
        relation_label: "Relacionat amb",
        other_item_id: SIP_DDI,
        other_item_title: "Troncal SIP - numeració múltiple DDI/DID",
        note: "Relacionats, no equivalents (annex M21).",
        foldable: false,
        created_at: "2026-09-27T20:24:09Z",
      },
    ],
  }),
  requirement({
    canonical_id: SIP_DDI,
    title: "Troncal SIP - numeració múltiple DDI/DID",
    pages: [31],
    evidence: [{ document: PPT, page: 31, section: null, quote: "numeració múltiple DDI", status_label: "Al document, sense valors" }],
  }),
  requirement({
    canonical_id: "m09",
    title: "Connectivitat a nivell 2 amb el datacenter pels equipaments municipals",
    description: "Requeriment que els equipaments municipals tinguin connectivitat a nivell 2 amb el datacenter.",
    evidence: [
      { document: PPT, page: 31, section: null, quote: "connectivitat a nivell 2 amb el datacenter", status_label: "Al document, sense valors" },
      manualCitation(22, "8.1.1.4", "hauran de disposar de connectivitat a nivell 2"),
    ],
    pages: [22, 31],
    sections: ["8.1.1.4"],
    review_revision: 2,
  }),
  requirement({
    canonical_id: "r-recording",
    title: "Solució d'enregistrament de trucades - requeriments tècnics",
    verification_status: "needs_review",
    verification_label: "Cal revisar",
    needs_review: true,
    values: [
      {
        key: "extensions_simultanies_modificables",
        operator: "exists",
        value: true,
        unit_label: null,
        source_label: "Del document",
        status_label: "Al document, valor per confirmar",
      },
    ],
    evidence_label: "Al document, valor per confirmar",
    pages: [26],
  }),
  requirement({
    canonical_id: "r-solvency",
    title: "Volum anual de negocis mínim",
    category: "economic_solvency",
    category_label: "Solvència econòmica",
    obligation_label: "Condicional",
    documents: [PCAP],
    pages: [9],
    values: [
      {
        key: "volum_negoci",
        operator: "gte",
        value: 450000,
        unit_label: "EUR",
        source_label: "Del document",
        status_label: "Confirmat al document",
      },
    ],
    evidence_label: "Confirmat al document",
  }),
];

// --------------------------------------------------------------------------- //
// Criteris d'adjudicació                                                        //
// --------------------------------------------------------------------------- //

function ref(id: string, name: string, points: number | null, document = PCAP, page = 15): AwardItemRef {
  return { canonical_id: id, name, points, documents: [document], pages: [page] };
}

const QUALITATIVE = ref("a-qual", "Criteris qualitatius", 20, PCAP, 14);
const MOBILE = ref("a-c7", "Minuts mensuals per mòbil 20000 minuts", 1);
const CATALAN = ref("a-c8", "Comptar amb centre de suport i atenció en català", 1);

function award(
  overrides: Partial<CanonicalAwardCriterion> & Pick<CanonicalAwardCriterion, "canonical_id" | "name" | "points">,
): CanonicalAwardCriterion {
  return {
    description: null,
    role: "scored",
    role_label: "Puntua",
    counts_in_total: true,
    parent: null,
    children: [],
    folded_into: null,
    restatements: [],
    mechanism_status: "verified",
    mechanism_label: "Puntuació confirmada al document",
    formula: null,
    formula_status: null,
    formula_label: null,
    provenance: "automated",
    provenance_label: "Extret automàticament",
    verification_status: "unreviewed",
    verification_label: "Pendent de revisar",
    evidence: [],
    documents: [PCAP],
    pages: [15],
    review_revision: 2,
    ...overrides,
  };
}

const CHILDREN: Array<[string, string, number]> = [
  ["a-c1", "Accés a Internet descentralitzat xDSL/GPON 1Gbps simètric als equipaments municipals", 5],
  ["a-c2", "Tarifa 1 a 20 GB de dades", 4],
  ["a-c3", "Tarifa 2 a dades il·limitades", 3],
  ["a-c4", "Accés a Internet descentralitzat xDSL/GPON 1Gbps a les fibres individuals", 2],
  ["a-c5", "Ampliació bonus tarifaris de trànsit de fixos - 15.000 minuts a destinacions fixes nacionals", 2],
  ["a-c6", "Ampliació bonus tarifaris de trànsit de fixos - 15.000 minuts a destinacions mòbils nacionals", 2],
  [MOBILE.canonical_id, MOBILE.name, 1],
  [CATALAN.canonical_id, CATALAN.name, 1],
];

const PRICE = award({
  canonical_id: "a-price",
  name: "Criteri preu",
  points: 80,
  pages: [14],
  formula: "Vi = Bi × 80 punts / Max(Bs, Bmax)",
  formula_status: "not_evidence_verified",
  formula_label: "Fórmula detectada, pendent de verificar",
});

const INFORME_MOBILE = ref("a-i1", "Servei de trànsit de veu de mòbils - Minuts mensuals per mòbil 20000 minuts", 1, INFORME, 6);
const INFORME_CATALAN = ref("a-i2", "Suport - Comptar amb centre de suport i atenció en català", 1, INFORME, 6);

const scoredChildren = CHILDREN.map(([id, name, points]) =>
  award({
    canonical_id: id,
    name,
    points,
    parent: QUALITATIVE,
    restatements: id === MOBILE.canonical_id ? [INFORME_MOBILE] : id === CATALAN.canonical_id ? [INFORME_CATALAN] : [],
  }),
);

const CHECKS = [
  {
    code: "formula_not_evidence_verified",
    label: "Fórmula detectada, pendent de verificar",
    item_ids: [PRICE.canonical_id],
    details: {},
  },
  {
    code: "restatement_excluded",
    label: "Reiteració exclosa del total (no es compta dues vegades)",
    item_ids: [INFORME_MOBILE.canonical_id, INFORME_CATALAN.canonical_id],
    details: { points_excluded: 2 },
  },
];

export const awardScoring: AwardCriteriaView = {
  view: "scoring",
  criteria: [PRICE, ...scoredChildren],
  scored_count: 9,
  total_points: 100,
  checks: CHECKS,
};

function notCounting(id: string, name: string, role: string, roleLabel: string, extra: Partial<CanonicalAwardCriterion> = {}) {
  return award({ canonical_id: id, name, points: null, role, role_label: roleLabel, counts_in_total: false, ...extra });
}

export const awardAll: AwardCriteriaView = {
  view: "all",
  criteria: [
    PRICE,
    ...scoredChildren,
    award({
      canonical_id: QUALITATIVE.canonical_id,
      name: QUALITATIVE.name,
      points: 20,
      role: "parent_total",
      role_label: "Total del bloc",
      counts_in_total: false,
      children: CHILDREN.map(([id, name, points]) => ref(id, name, points)),
    }),
    award({
      canonical_id: "a-iq",
      name: "Criteris qualitatius (fins a 20 punts)",
      points: 20,
      role: "parent_total",
      role_label: "Total del bloc",
      counts_in_total: false,
      folded_into: QUALITATIVE,
      documents: [INFORME],
      pages: [6],
    }),
    award({ ...INFORME_MOBILE, counts_in_total: false, folded_into: MOBILE }),
    award({ ...INFORME_CATALAN, counts_in_total: false, folded_into: CATALAN }),
    notCounting("a-offer", "Criteris qualitatius del model d'oferta (Sobre 2) - opcions SI/NO", "offer_form_view", "Model d'oferta", {
      folded_into: QUALITATIVE,
    }),
    ...[
      "Pluralitat de criteris d'adjudicació: preu i criteris qualitatius",
      "Compliment de prestacions de caràcter qualitatiu puntuades a l'oferta",
      "Preferència en cas d'empat per treballadors amb discapacitat",
      "Criteris d'adjudicació (component preu i criteris qualitatius) - baixa anormal",
      "Criteris d'adjudicació susceptibles de ser aplicats mitjançant fórmules",
      "Backup 4G/5G per serveis de veu",
      "Cost total del servei (estudi econòmic)",
    ].map((name, index) => notCounting(`a-n${index}`, name, "not_scored", "No puntua")),
    notCounting("a-d1", "Auriculars biaurals amb connexió jack", "desirable", "Millora valorable"),
    notCounting("a-d2", "Eficiència energètica dels telèfons", "desirable", "Millora valorable"),
  ],
  scored_count: 9,
  total_points: 100,
  checks: CHECKS,
};
