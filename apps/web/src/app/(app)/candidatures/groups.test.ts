import { describe, expect, it } from "vitest";

import type { WorkspaceStatus, WorkspaceSummary } from "@/lib/workspace";

import { attentionPills, decisionPill, groupWorkspaces, nextStep, summaryLine } from "./groups";

function workspace(overrides: Partial<WorkspaceSummary>): WorkspaceSummary {
  return {
    id: "w",
    company_id: "c1",
    tender_id: "t",
    tender_lot_id: null,
    status: "awaiting_decision",
    tender_title: "Licitació",
    expediente_number: "EXP-1",
    buyer_name: "Ajuntament Demo",
    deadline_at: null,
    days_remaining: null,
    deadline_passed: false,
    prefit_score: 88,
    eligibility: "eligible",
    recommendation: "go_candidate",
    human_decision: null,
    analysis_basis: "real",
    analysis_outdated: false,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

const ALL_STATUSES: WorkspaceStatus[] = [
  "analysis",
  "needs_review",
  "awaiting_decision",
  "go_approved",
  "no_go_approved",
  "preparing_bid",
  "ready_for_final_review",
  "approved_for_submission",
  "submitted",
  "withdrawn",
];

describe("groupWorkspaces", () => {
  it("cada candidatura cau en un sol grup derivat de stepOf, en l'ordre Decidir → Preparant → Per tancar → Tancades", () => {
    const groups = groupWorkspaces(ALL_STATUSES.map((status) => workspace({ id: status, status })));
    expect(groups.map((group) => group.label)).toEqual(["Per decidir", "Preparant", "Per tancar", "Tancades"]);
    expect(groups.map((group) => group.rows.map((row) => row.id))).toEqual([
      ["analysis", "needs_review", "awaiting_decision"],
      ["go_approved", "preparing_bid"],
      ["ready_for_final_review", "approved_for_submission"],
      ["no_go_approved", "submitted", "withdrawn"],
    ]);
    expect(groups.reduce((sum, group) => sum + group.rows.length, 0)).toBe(ALL_STATUSES.length);
    expect(groups[3].collapsed).toBe(true);
  });

  it("dins d'un grup ordena per termini ascendent i deixa les que no en tenen al final", () => {
    const groups = groupWorkspaces([
      workspace({ id: "none", days_remaining: null }),
      workspace({ id: "far", days_remaining: 30 }),
      workspace({ id: "soon", days_remaining: 3 }),
    ]);
    expect(groups[0].rows.map((row) => row.id)).toEqual(["soon", "far", "none"]);
  });

  it("omet els grups buits i no plega «Tancades» si és l'únic grup", () => {
    const groups = groupWorkspaces([workspace({ id: "s", status: "submitted" })]);
    expect(groups.map((group) => group.key)).toEqual(["tancada"]);
    expect(groups[0].collapsed).toBe(false);
  });
});

describe("decisió humana vs. recomanació automàtica", () => {
  it("sense decisió humana → «Pendent» en atenció, mai en majúscules", () => {
    expect(decisionPill(workspace({ human_decision: null }))).toEqual({ level: "attention", label: "Pendent" });
  });

  it("amb decisió → etiqueta i nivell del diccionari; GO, NO-GO i ajornada són diferents", () => {
    expect(decisionPill(workspace({ human_decision: "go" }))).toEqual({ level: "ready", label: "GO" });
    expect(decisionPill(workspace({ human_decision: "no_go" }))).toEqual({ level: "neutral", label: "NO-GO" });
    expect(decisionPill(workspace({ human_decision: "defer" }))).toEqual({ level: "attention", label: "Ajornada" });
  });

  it("una candidatura tancada sense decisió registrada diu «Sense decisió», no «Pendent»", () => {
    expect(decisionPill(workspace({ status: "withdrawn", human_decision: null }))).toEqual({ level: "neutral", label: "Sense decisió" });
  });
});

describe("attentionPills: només camps reals", () => {
  it("cap avís si no hi ha res", () => {
    expect(attentionPills(workspace({}))).toEqual([]);
  });

  it("anàlisi endarrerida, elegibilitat NO-GO automàtica i termini tancat, amb els nivells del diccionari", () => {
    const pills = attentionPills(workspace({ analysis_outdated: true, eligibility: "no_go", deadline_passed: true, days_remaining: -2 }));
    expect(pills).toEqual([
      { level: "blocking", label: "Termini tancat" },
      { level: "blocking", label: "Elegibilitat: NO-GO" },
      { level: "attention", label: "Anàlisi endarrerida" },
    ]);
  });

  it("undetermined no és cap avís ni cap «ok»", () => {
    expect(attentionPills(workspace({ eligibility: "undetermined" }))).toEqual([]);
  });
});

describe("nextStep: derivat només de l'estat", () => {
  it.each<[WorkspaceStatus, string]>([
    ["analysis", "Obre"],
    ["needs_review", "Revisa"],
    ["awaiting_decision", "Decideix"],
    ["go_approved", "Prepara"],
    ["preparing_bid", "Prepara"],
    ["ready_for_final_review", "Revisa el dossier"],
    ["approved_for_submission", "Obre"],
    ["submitted", "Obre"],
    ["no_go_approved", "Obre"],
    ["withdrawn", "Obre"],
  ])("%s → %s", (status, label) => {
    expect(nextStep(workspace({ status }))).toBe(label);
  });
});

describe("summaryLine", () => {
  it("compta obertes i tancades", () => {
    expect(summaryLine([workspace({ status: "analysis" }), workspace({ status: "submitted" }), workspace({ status: "preparing_bid" })])).toBe(
      "3 candidatures · 2 obertes · 1 tancada",
    );
    expect(summaryLine([workspace({ status: "analysis" })])).toBe("1 candidatura · 1 oberta · 0 tancades");
  });
});
