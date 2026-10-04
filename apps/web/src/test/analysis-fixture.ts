import type { AnalysisRun, TenderStructureView } from "@/lib/analysis";
import type { RequirementAnalysis } from "@/lib/requirements";

/**
 * Id SINTÈTIC amb forma d'UUID real (el requisit arxivat): així l'assert «cap UUID visible»
 * té alguna cosa a enxampar si un component pinta l'id en lloc del nom.
 */
export const GONE_REQUIREMENT_ID = "7f3c2a9e-5b1d-4e8f-9a6c-2d4b8e1f0a37";

/** Una anàlisi SINTÈTICA: cap dada real. */
export function analysisFixture(overrides: Partial<AnalysisRun> = {}): AnalysisRun {
  return {
    id: "run-1",
    tender_id: "t1",
    rules_version: "phase3a-analysis-v2",
    is_current: true,
    created_at: "2026-10-02T10:00:00Z",
    stale: false,
    summary: {
      fit: { satisfies: 1, does_not_satisfy: 1, unknown: 2, not_applicable: 0 },
      blocker_potential_count: 1,
      review_required_count: 1,
      secured_points: 2,
      at_risk_points: 3,
      lost_points: 0,
      not_assessable_points: 10,
      max_points: 15,
      delivery: { available: 1, pending: 1, unavailable: 0 },
      questions: { buyer: 1, company: 1 },
    },
    fit_assessments: [
      { id: "f1", requirement_id: "r-ok", fit_result: "satisfies", knowledge_source: "current_verified", proof_status: "sufficient", territory_verdict: "not_applicable", verification_status: "verified", rule_code: "fact_requirement_v1", explanation_code: "FACT_TRUE_VERIFIED", support_json: null },
      { id: "f2", requirement_id: "r-no", fit_result: "does_not_satisfy", knowledge_source: "current_declared", proof_status: "unknown", territory_verdict: "not_applicable", verification_status: "not_verified", rule_code: "fact_requirement_v1", explanation_code: "FACT_CONTRADICTS", support_json: null },
      { id: "f3", requirement_id: "r-turnover", fit_result: "unknown", knowledge_source: "none", proof_status: "missing", territory_verdict: "not_applicable", verification_status: "unknown", rule_code: "turnover_requirement_v1", explanation_code: "TURNOVER_UNKNOWN", support_json: null },
      { id: "f4", requirement_id: GONE_REQUIREMENT_ID, fit_result: "unknown", knowledge_source: "none", proof_status: "unknown", territory_verdict: "unknown", verification_status: "unknown", rule_code: "unstructured_requirement_v1", explanation_code: "NO_STRUCTURE_TO_COMPARE", support_json: null },
    ],
    need_assessments: [],
    criterion_assessments: [
      { id: "c1", award_criterion_id: "k-auto", criterion_result: "needs_review", knowledge_source: "current_declared", proof_status: "unknown", points_status: "at_risk", max_points: 5, secured_points: 2, at_risk_points: 3, lost_points: 0, not_assessable_points: 0, rule_code: "criterion_from_needs_v1", explanation_code: "CRITERION_AT_RISK", support_json: null },
      { id: "c2", award_criterion_id: "k-judgement", criterion_result: "not_assessable", knowledge_source: "not_applicable", proof_status: "not_required", points_status: "not_assessable", max_points: 10, secured_points: 0, at_risk_points: 0, lost_points: 0, not_assessable_points: 10, rule_code: "criterion_judgement_v1", explanation_code: "CRITERION_NOT_ASSESSABLE_JUDGEMENT", support_json: null },
    ],
    delivery_candidates: [
      { id: "d1", scope_item_id: "s-ftth", company_service_id: "svc-1", partner_role_id: null, candidate_type: "own_current", origin: "current_knowledge", readiness: "available", territory_verdict: "matched", area_verdicts_json: null, rule_code: "delivery_from_services_v1", explanation_code: "DELIVERY_OWN_CURRENT" },
      { id: "d2", scope_item_id: "s-voice", company_service_id: null, partner_role_id: null, candidate_type: "subcontract_unknown", origin: "hypothesis", readiness: "pending_partner", territory_verdict: "unknown", area_verdicts_json: null, rule_code: "delivery_from_services_v1", explanation_code: "DELIVERY_NO_PARTNER_ROLE" },
    ],
    findings: [
      { id: "g-warn", finding_type: "warning", rule_code: "demo_dependency_warning_v1", explanation_code: "DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES", requirement_id: null, criterion_id: "k-auto", scope_item_id: null, details_json: null },
      { id: "g-block", finding_type: "blocker_potential", rule_code: "blocker_potential_v1", explanation_code: "MANDATORY_DOES_NOT_SATISFY", requirement_id: "r-no", criterion_id: null, scope_item_id: null, details_json: null },
      { id: "g-info", finding_type: "missing_information", rule_code: "missing_information_v1", explanation_code: "TURNOVER_UNKNOWN", requirement_id: "r-turnover", criterion_id: null, scope_item_id: null, details_json: null },
    ],
    questions: [
      { id: "q1", audience: "buyer", question_code: "buyer.provide_territory_list", rule_code: "territory_identity_v1", requirement_id: "r-no", criterion_id: null, scope_item_id: null, params_json: {}, text: "Demaneu a l'organ la llista de territoris del requisit «Cobertura»." },
      { id: "q2", audience: "company", question_code: "company.confirm_fact", rule_code: "fact_requirement_v1", requirement_id: "r-turnover", criterion_id: null, scope_item_id: null, params_json: {}, text: "Confirmeu si l'empresa compleix «Suport 24x7» (requisit «Suport»)." },
    ],
    ...overrides,
  };
}

/** El mateix run, amb la versió v3 del copy i findings de falta d'informació amb origen. */
export function analysisV3Fixture(overrides: Partial<AnalysisRun> = {}): AnalysisRun {
  const base = analysisFixture();
  const missing = (id: string, code: string, details: Record<string, unknown> | null) => ({
    id,
    finding_type: "missing_information",
    rule_code: "missing_information_v1",
    explanation_code: code,
    requirement_id: null,
    criterion_id: null,
    scope_item_id: null,
    details_json: details,
  });
  return {
    ...base,
    rules_version: "phase3a-analysis-v3",
    findings: [
      missing("g-tender", "TENDER_REFERENCE_DATE_MISSING", { missing_from: "tender" }),
      missing("g-company", "DATA_MISSING", { missing_from: "company" }),
      missing("g-unattributed", "DATA_MISSING_UNATTRIBUTED", null),
    ],
    ...overrides,
  };
}

/** Requisits i criteris SINTÈTICS. `GONE_REQUIREMENT_ID` no hi és a propòsit (arxivat després de l'anàlisi). */
export const requirementsFixture = {
  run: null,
  is_stale: false,
  award_candidates: [],
  requirements: [
    { id: "r-ok", title: "Suport 24x7", category: "technical", origin: "extracted", citations: [], evidence: [{ id: "e1", extraction_id: "x1", page_id: "p1", tender_document_id: "doc1", page_number: 7, quote: "Suport 24x7", char_start: null, char_end: null, section: null, evidence_role: "primary", extraction_revision: 1, document_name: "Plec tècnic.pdf" }] },
    { id: "r-no", title: "Cobertura al municipi", category: "technical", origin: "extracted", citations: [], evidence: [] },
    { id: "r-turnover", title: "Volum de negoci mínim", category: "economic_solvency", origin: "manual", evidence: [], citations: [{ id: "sr1", ref_kind: "extracted_page", document_id: "doc2", document_name: "Plec administratiu.pdf", page_number: 3, quote: null, file_id: null, version_number: null, content_sha256: "x", extraction_id: null, page_id: null, location_label: null, char_start: null, char_end: null, created_at: "2026-10-01T00:00:00Z" }] },
  ],
  award_criteria: [
    { id: "k-auto", name: "Millora de canals", points: 5, category: "improvement", evidence: [] },
    { id: "k-judgement", name: "Memòria tècnica", points: 10, category: "quality", evidence: [] },
  ],
} as unknown as RequirementAnalysis;

export const structureFixture: TenderStructureView = {
  blocks: [{ id: "b1", code: "B1", name: "Bloc de fibra" }],
  sites: [{ id: "site-1", code: "U01", name: "Seu sintètica" }],
  scope_items: [
    { id: "s-ftth", service_line_code: "access_ftth", block_id: "b1", site_id: "site-1" },
    { id: "s-voice", service_line_code: "voice_pbx", block_id: null, site_id: null },
  ],
};
