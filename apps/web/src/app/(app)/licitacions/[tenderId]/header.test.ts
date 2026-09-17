import { describe, expect, it } from "vitest";

import type { Compliance, ComplianceRun } from "@/lib/compliance";
import { formatMoney } from "@/lib/format";
import type { ExtractionRun, RequirementAnalysis } from "@/lib/requirements";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

import {
  TABS,
  amountMeta,
  candidatureMeta,
  deadlineMeta,
  requirementsLine,
  tabFromParam,
  tabItems,
} from "./header";

function run(overrides: Partial<ComplianceRun>): ComplianceRun {
  return {
    id: "r1",
    company_id: "c1",
    tender_id: "t1",
    status: "completed",
    is_current: true,
    rules_version: "v1",
    company_profile_fingerprint: "a",
    requirements_fingerprint: "b",
    started_at: "2026-09-10T10:00:00Z",
    completed_at: "2026-09-10T10:01:00Z",
    duration_ms: 1000,
    requirements_total: 14,
    mandatory_total: 10,
    satisfies_count: 9,
    does_not_satisfy_count: 2,
    unknown_count: 0,
    needs_review_count: 3,
    not_applicable_count: 0,
    potential_blockers_count: 0,
    confirmed_blockers_count: 2,
    overall_result: "no_go",
    recommendation: "no_go",
    prefit_score: 92,
    error_code: null,
    error_message: null,
    ...overrides,
  } as ComplianceRun;
}

function compliance(overrides: Partial<ComplianceRun> | null): Compliance {
  return { run: overrides === null ? null : run(overrides), assessments: [], is_stale: false };
}

function analysis(overrides: Partial<ExtractionRun> | null): RequirementAnalysis {
  const base = {
    id: "x1",
    status: "completed",
    started_at: "2026-09-09T10:00:00Z",
    completed_at: "2026-09-09T10:05:00Z",
    requirements_extracted: 14,
    skipped_documents: [],
  } as unknown as ExtractionRun;
  return { run: overrides === null ? null : ({ ...base, ...overrides } as ExtractionRun), requirements: [], award_criteria: [], is_stale: false };
}

describe("pestanyes", () => {
  it("són les quatre aprovades, en ordre, i la URL només accepta les seves claus", () => {
    expect(TABS.map((tab) => tab.key)).toEqual(["resum", "plecs", "requisits", "historial"]);
    expect(TABS.map((tab) => tab.label)).toEqual(["Resum", "Plecs", "Requisits", "Historial"]);
    expect(tabFromParam("requisits")).toBe("requisits");
    expect(tabFromParam("historial")).toBe("historial");
    expect(tabFromParam(null)).toBe("resum");
    expect(tabFromParam("inventada")).toBe("resum");
  });

  it("els recomptes surten de dades reals: plecs = documents, requisits = extrets; bloqueig només si n'hi ha", () => {
    const items = tabItems({ documents: 3, analysis: analysis({}), compliance: compliance({ confirmed_blockers_count: 2 }) });
    expect(items.find((item) => item.key === "plecs")?.count).toBe(3);
    expect(items.find((item) => item.key === "requisits")).toMatchObject({ count: 14, countLevel: "blocking" });
    expect(items.find((item) => item.key === "historial")?.count).toBeUndefined();
    const clean = tabItems({ documents: 0, analysis: analysis({}), compliance: compliance({ confirmed_blockers_count: 0 }) });
    expect(clean.find((item) => item.key === "requisits")?.countLevel).toBeUndefined();
    const none = tabItems({ documents: 2, analysis: null, compliance: null });
    expect(none.find((item) => item.key === "requisits")?.count).toBeUndefined();
  });
});

describe("línia de requisits de la capçalera", () => {
  it("sense anàlisi: «Plec sense analitzar» i cap detall", () => {
    expect(requirementsLine(null, null)).toEqual({ text: "Plec sense analitzar", detail: null, level: "neutral" });
  });

  it("amb compliment avaluat: bloquegen en vermell només si n'hi ha", () => {
    expect(requirementsLine(analysis({}), compliance({ confirmed_blockers_count: 2, needs_review_count: 3 }))).toMatchObject({
      detail: "2 bloquegen · 3 per revisar",
      level: "blocking",
    });
    expect(requirementsLine(analysis({}), compliance({ confirmed_blockers_count: 0, needs_review_count: 1, overall_result: "review_required" }))).toMatchObject({
      detail: "0 bloquegen · 1 per revisar",
      level: "neutral",
    });
  });

  it("undetermined no és cap OK: «encara no avaluats»", () => {
    expect(requirementsLine(analysis({}), compliance({ overall_result: "undetermined", confirmed_blockers_count: 0 }))).toMatchObject({
      detail: "encara no avaluats",
      level: "neutral",
    });
  });
});

describe("termini, import i candidatura", () => {
  it("termini nul és desconegut, no bloqueig", () => {
    expect(deadlineMeta(null)).toEqual({ level: "neutral", text: "Sense termini", date: null });
  });

  it("termini real: dies i data, nivell del diccionari", () => {
    const soon = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString();
    expect(deadlineMeta(soon)).toMatchObject({ level: "blocking", text: "5 dies", date: formatDateTime(soon) });
    const past = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
    expect(deadlineMeta(past)).toMatchObject({ level: "blocking", text: "Tancat" });
  });

  it("import: valor estimat, si no pressupost base, si no «—» sense inferir", () => {
    expect(amountMeta({ estimated_value: "240000", base_budget: "200000", currency: "EUR" })).toEqual({ text: formatMoney("240000", "EUR"), note: "valor estimat" });
    expect(amountMeta({ estimated_value: null, base_budget: "200000", currency: "EUR" })).toEqual({ text: formatMoney("200000", "EUR"), note: "pressupost base" });
    expect(amountMeta({ estimated_value: null, base_budget: null, currency: "EUR" })).toEqual({ text: "—", note: null });
  });

  it("candidatura només si existeix; la decisió humana només si és real", () => {
    expect(candidatureMeta(null)).toBeNull();
    const ws = { status: "preparing_bid", human_decision: "go" } as WorkspaceDetail;
    expect(candidatureMeta(ws)).toEqual({ status: { level: "running", label: "Preparant oferta" }, decision: { level: "ready", label: "GO" } });
    const pending = { status: "awaiting_decision", human_decision: null } as WorkspaceDetail;
    expect(candidatureMeta(pending)).toEqual({ status: { level: "attention", label: "Pendent de decisió" }, decision: null });
  });
});
