import { describe, expect, it } from "vitest";

import type { Assessment, AssessmentResult, BlockerStatus, Compliance, ComplianceRun } from "@/lib/compliance";
import {
  documentsSummary,
  envelopeOf,
  folderSummary,
  groupChecklist,
  nextActions,
  offerSummary,
  personnelPending,
  solvencyTopics,
  technicalSummary,
} from "@/lib/envelopes";
import type { ReadinessFinding } from "@/lib/readiness";
import { lotFixture, offerFixture, totalsFixture } from "@/test/economic-fixture";
import { solutionFixture } from "@/test/technical-fixture";
import type { ChecklistItem } from "@/lib/workspace";

function item(id: string, category: string, overrides: Partial<ChecklistItem> = {}): ChecklistItem {
  return {
    id,
    workspace_id: "w1",
    requirement_id: `r-${id}`,
    title: `Ítem ${id}`,
    category,
    mandatory: true,
    status: "pending",
    source: "requirement",
    assigned_to: null,
    assigned_to_name: null,
    due_at: null,
    note: null,
    completed_at: null,
    created_at: "2026-10-30T09:00:00Z",
    ...overrides,
  };
}

function assessment(
  id: string,
  category: string,
  result: AssessmentResult,
  overrides: Partial<Assessment> = {},
  blocker: BlockerStatus = "none",
): Assessment {
  return {
    id,
    assessment_run_id: "run1",
    requirement_id: `req-${id}`,
    tender_id: "t1",
    tender_lot_id: null,
    automatic_result: result,
    reviewed_result: null,
    effective_result: result,
    assessment_mode: "automatic",
    rule_code: "rule",
    explanation_code: "CODE",
    explanation_params: null,
    company_value_json: null,
    requirement_value_json: null,
    obligation: "mandatory",
    blocker_status: blocker,
    evidence_strength: "missing",
    review_status: "unreviewed",
    review_note: null,
    reviewed_at: null,
    reviewed_by: null,
    reviewed_by_name: null,
    calculated_at: "2026-10-30T09:00:00Z",
    evidence: [],
    requirement: {
      requirement_id: `req-${id}`,
      title: `Requisit ${id}`,
      category,
      review_status: "unreviewed",
      tender_document_id: null,
      extraction_id: null,
      extraction_revision: null,
      page_number: null,
      quote: null,
      document_name: null,
    },
    ...overrides,
  };
}

function compliance(assessments: Assessment[], run: Partial<ComplianceRun> = {}): Compliance {
  return {
    run: {
      id: "run1",
      company_id: "c1",
      tender_id: "t1",
      status: "completed",
      is_current: true,
      rules_version: "company-compliance-v6",
      company_profile_fingerprint: "f",
      requirements_fingerprint: "f",
      started_at: "2026-10-30T09:00:00Z",
      completed_at: "2026-10-30T09:00:01Z",
      duration_ms: 1000,
      requirements_total: assessments.length,
      mandatory_total: assessments.length,
      satisfies_count: 0,
      does_not_satisfy_count: 0,
      unknown_count: 0,
      needs_review_count: 0,
      not_applicable_count: 0,
      potential_blockers_count: 0,
      confirmed_blockers_count: 0,
      overall_result: "review_required",
      recommendation: "review",
      prefit_score: null,
      error_code: null,
      error_message: null,
      ...run,
    },
    assessments,
    groups: [],
    is_stale: false,
  };
}

describe("checklist per sobres", () => {
  it("cada categoria va al seu sobre; el que no se sap va a execució, mai s'endevina", () => {
    expect(envelopeOf({ category: "administrative_documentation" })).toBe("A");
    expect(envelopeOf({ category: "economic_solvency" })).toBe("A");
    expect(envelopeOf({ category: "personnel" })).toBe("A");
    expect(envelopeOf({ category: "insurance" })).toBe("A");
    expect(envelopeOf({ category: "technical_requirement" })).toBe("B");
    expect(envelopeOf({ category: "contract_condition" })).toBe("execucio");
    expect(envelopeOf({ category: "other" })).toBe("execucio");
    expect(envelopeOf({ category: "una_categoria_nova" })).toBe("execucio");
  });

  it("quatre carpetes sempre, en ordre, amb els recomptes i el resum de capçalera", () => {
    const folders = groupChecklist([
      item("1", "administrative_documentation", { status: "completed" }),
      item("2", "certification"),
      item("3", "technical_requirement", { status: "blocked" }),
      item("4", "contract_condition", { mandatory: false }),
    ]);
    expect(folders.map((folder) => [folder.key, folder.total])).toEqual([["A", 2], ["B", 1], ["C", 0], ["execucio", 1]]);
    expect(folderSummary(folders[0])).toBe("1 de 2 a punt · 1 obligatori pendent");
    expect(folderSummary(folders[1])).toBe("0 de 1 a punt · 1 obligatori pendent · 1 bloquejat");
    expect(folderSummary(folders[2])).toBe("Cap ítem");
    expect(folderSummary(folders[3])).toBe("0 de 1 a punt");
  });
});

describe("Sobre A: solvència i documents", () => {
  it("un tema per categoria, amb el veredicte del compliment i el que l'empresa declara", () => {
    const topics = solvencyTopics(
      compliance([
        assessment("a", "economic_solvency", "satisfies"),
        assessment("b", "economic_solvency", "does_not_satisfy", {}, "potential"),
        assessment("c", "economic_solvency", "needs_review"),
        assessment("d", "certification", "needs_review", {
          explanation_params: { company_certifications: ["ENS categoria Mitjana"] },
        }),
        assessment("e", "insurance", "satisfies"),
        assessment("f", "insurance", "satisfies", { obligation: "informative" }),
      ]),
    );
    const byKey = Object.fromEntries(topics.map((topic) => [topic.key, topic]));
    expect(byKey.economic.text).toBe("1 compleix · 1 no compleix · 1 per revisar");
    expect(byKey.economic.tone).toBe("blocking");
    expect(byKey.certification.text).toBe("1 per revisar");
    expect(byKey.certification.tone).toBe("attention");
    expect(byKey.certification.companyHas).toBe("ENS categoria Mitjana");
    // Només els obligatoris: l'informatiu no compta.
    expect(byKey.insurance.text).toBe("Compleix");
    expect(byKey.insurance.tone).toBe("ready");
    expect(byKey.classification.text).toBe("El plec no en demana");
  });

  it("sense compliment calculat no hi ha temes (ni cap «compleix» inventat)", () => {
    expect(solvencyTopics(null)).toEqual([]);
    expect(solvencyTopics({ run: null, assessments: [], is_stale: false })).toEqual([]);
  });

  it("documents obligatoris del dossier; sense anàlisi no es diu res", () => {
    expect(
      documentsSummary({
        analysis_state: "current",
        mandatory_total: 140,
        covered: 12,
        missing_document: 120,
        needs_review: 8,
        not_applicable: 0,
        expired_documents: 0,
        optional_total: 0,
        optional_covered: 0,
      }),
    ).toEqual({ text: "12 de 140 obligatoris coberts · 120 sense document · 8 per revisar", tone: "attention" });
    expect(
      documentsSummary({
        analysis_state: "absent",
        mandatory_total: null,
        covered: 0,
        missing_document: 0,
        needs_review: 0,
        not_applicable: 0,
        expired_documents: 0,
        optional_total: 0,
        optional_covered: 0,
      }),
    ).toBeNull();
  });
});

describe("Sobre B: capítols de la memòria", () => {
  it("compta redactats, validats, punts del títol i marques «Cal confirmar»", () => {
    const summary = technicalSummary([
      solutionFixture({ id: "s1", title: "1.b · Fixa (8 punts)", status: "needs_validation", proposed_solution: "Text. [Cal confirmar: persones.] I [Cal confirmar la cobertura.]" }),
      solutionFixture({ id: "s2", title: "2 · Explotació (5 punts)", status: "validated" }),
      solutionFixture({ id: "s3", title: "Sense punts", status: "validated", validation_stale: true, proposed_solution: "" }),
    ]);
    expect(summary).toEqual({ total: 3, drafted: 2, validated: 1, awaitingValidation: 1, stale: 1, points: 13, confirmations: 2 });
    expect(technicalSummary(null).points).toBeNull();
  });

  it("el personal que el compliment no dona per bo és una alerta", () => {
    const result = personnelPending(
      compliance([
        assessment("p1", "personnel", "needs_review", { requirement: { ...assessment("p1", "personnel", "needs_review").requirement!, title: "Cap de projecte: enginyer de telecomunicacions" } }),
        assessment("p2", "personnel", "satisfies"),
        assessment("p3", "professional_qualification", "unknown"),
      ]),
      1,
    );
    expect(result).toEqual({ total: 2, titles: ["Cap de projecte: enginyer de telecomunicacions"] });
  });
});

describe("Sobre C: oferta", () => {
  it("preu, PBL, baixa i marge de les línies d'un lot únic", () => {
    const summary = offerSummary(
      offerFixture({
        notes: "Escandall: marge net previst a part.",
        totals: totalsFixture({ total_revenue: "122914.41", line_count: 113 }),
        internal_economics: { total_cost: "55476.83", margin_amount: "67437.58", margin_on_sales_pct: "54.8655", markup_on_cost_pct: "121.5599", incomplete_cost_lines: 0 },
        lots: [lotFixture({ limit: { tender_max: "175000.00", difference_to_maximum: "52085.59", exceeds_lot_max: false } })],
      }),
    );
    expect(summary).toMatchObject({
      revenue: "122914.41",
      tenderMax: "175000.00",
      discountPct: "29.76",
      verdict: "within",
      lines: 113,
      margin: "67437.58",
      marginPct: "54.8655",
      hasNotes: true,
    });
  });

  it("sense màxim no hi ha baixa; sense oferta, res", () => {
    const summary = offerSummary(offerFixture({ lots: [lotFixture({ limit: { tender_max: null, difference_to_maximum: null, exceeds_lot_max: null } })] }));
    expect(summary?.discountPct).toBeNull();
    expect(offerSummary(null)).toBeNull();
  });
});

describe("Què et falta per tancar l'oferta?", () => {
  const finding = (code: string, severity: ReadinessFinding["severity"] = "blocker", domain: ReadinessFinding["domain"] = "technical"): ReadinessFinding => ({
    code,
    severity,
    domain,
    entity_id: null,
    context: {},
  });

  it("un per codi amb el recompte; els avisos no hi són; el compliment i les marques s'hi afegeixen", () => {
    const actions = nextActions(
      [
        finding("technical_solution_unvalidated"),
        finding("technical_solution_unvalidated"),
        finding("mandatory_evidence_missing", "blocker", "dossier"),
        finding("economic_unit_limit_unknown", "info", "economic"),
      ],
      compliance([], { potential_blockers_count: 1 }),
      { total: 2, drafted: 2, validated: 0, awaitingValidation: 2, stale: 0, points: 16, confirmations: 3 },
    );
    expect(actions.map((action) => [action.key, action.count, action.countable])).toEqual([
      // Un avís agregat del dossier arriba una sola vegada: el seu «1» no és cap recompte.
      ["mandatory_evidence_missing", 1, false],
      ["technical_solution_unvalidated", 2, true],
      ["technical_confirmations", 3, true],
      ["compliance_blockers", 1, true],
    ]);
    expect(actions[0].destination).toEqual({ kind: "area", area: "dossier" });
    expect(actions[3].destination).toEqual({ kind: "tender", tab: "requisits" });
  });

  it("un avís per entitat sí que és un recompte, encara que n'hi hagi un de sol", () => {
    const single = nextActions([{ ...finding("technical_solution_unvalidated"), entity_id: "s1" }], null, null);
    expect(single).toMatchObject([{ key: "technical_solution_unvalidated", count: 1, countable: true }]);
  });

  it("un codi desconegut no s'amaga: surt amb el codi cru", () => {
    expect(nextActions([finding("codi_nou")], null, null)).toMatchObject([{ key: "codi_nou", label: "codi_nou", count: 1 }]);
  });
});
