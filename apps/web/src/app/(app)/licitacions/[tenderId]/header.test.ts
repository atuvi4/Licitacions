import { describe, expect, it } from "vitest";

import type { Compliance, ComplianceRun } from "@/lib/compliance";
import { formatAmount } from "@/lib/format";
import type { ExtractionRun, RequirementAnalysis } from "@/lib/requirements";
import { formatDateTime } from "@/lib/tenders";
import type { WorkspaceDetail } from "@/lib/workspace";

import {
  TABS,
  amountFact,
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
  it("són les cinc aprovades, en ordre, i la URL només accepta les seves claus", () => {
    expect(TABS.map((tab) => tab.key)).toEqual(["resum", "plecs", "requisits", "analisi", "historial"]);
    expect(TABS.map((tab) => tab.label)).toEqual(["Resum", "Plecs", "Requisits extrets", "Anàlisi", "Historial"]);
    expect(tabFromParam("requisits")).toBe("requisits");
    expect(tabFromParam("historial")).toBe("historial");
    expect(tabFromParam("analisi")).toBe("analisi");
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
  it("sense anàlisi: «Encara no hi ha cap anàlisi del plec» i cap detall (el mateix text que el dossier)", () => {
    expect(requirementsLine(null, null)).toEqual({ text: "Encara no hi ha cap anàlisi del plec", detail: null, level: "neutral" });
    expect(requirementsLine({ run: null, requirements: [], award_criteria: [], is_stale: false, analysis_state: "absent" }, null).text).toBe(
      "Encara no hi ha cap anàlisi del plec",
    );
  });

  it("sense run però amb requisits escrits a mà, `analysis_state` del servidor mana: hi ha anàlisi (M1)", () => {
    const manual = { id: "m", origin: "manual", archived_at: null, title: "RC", category: "insurance", obligation: "mandatory" } as unknown as RequirementAnalysis["requirements"][number];
    const line = requirementsLine({ run: null, requirements: [manual, { ...manual, id: "m2" }], award_criteria: [], is_stale: false, analysis_state: "current" }, null);
    expect(line.text).toBe("Requisits escrits a mà · 2");
    expect(line.text).not.toContain("Encara no hi ha cap anàlisi");
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

  it("termini real: dies naturals (regla única de lib/deadline), data i nivell del diccionari", () => {
    const now = new Date("2026-10-09T16:00:00Z");
    expect(deadlineMeta("2026-10-14T10:00:00Z", now)).toMatchObject({ level: "blocking", text: "5 dies", date: formatDateTime("2026-10-14T10:00:00Z") });
    // El cas del pilot: 11 dies i 18 hores són 12 dies naturals, aquí i a Candidatures.
    expect(deadlineMeta("2026-10-21T10:00:00Z", now)).toMatchObject({ text: "12 dies" });
    // Un termini passat no és cap urgència: neutre, mai vermell.
    expect(deadlineMeta("2026-10-06T10:00:00Z", now)).toMatchObject({ level: "neutral", text: "Fora de termini" });
  });

  it("import (P2-06): VEC, si no PBL sense IVA, si no PBL amb IVA, sempre amb la base; «no consta» sense inferir res", () => {
    expect(amountMeta({ estimated_value: "240000", base_budget: null, base_budget_without_tax: "240000", currency: "EUR" })).toEqual({
      text: formatAmount("240000", "EUR"),
      note: "valor estimat del contracte (VEC)",
      budget: null,
    });
    // El cas de les 79: només hi ha PBL sense IVA. Abans sortia «—».
    expect(amountMeta({ estimated_value: null, base_budget: null, base_budget_without_tax: "204232.75", currency: "EUR" })).toEqual({
      text: formatAmount("204232.75", "EUR"),
      note: "pressupost base de licitació sense IVA",
      budget: null,
    });
    expect(amountMeta({ estimated_value: null, base_budget: "200000", currency: "EUR" })).toEqual({
      text: formatAmount("200000", "EUR"),
      note: "pressupost base de licitació amb IVA",
      budget: null,
    });
    expect(amountMeta({ estimated_value: null, base_budget: null, currency: "EUR" })).toEqual({ text: "No consta a les dades de la font", note: null, budget: null });
    // Amb cèntims: el PBL de L'Escala no s'arrodoneix a 204.233 €.
    expect(amountMeta({ estimated_value: null, base_budget: null, base_budget_without_tax: "204232.75", currency: "EUR" }).text.replace(/\u00a0/g, " ")).toBe("204.232,75 €");
  });

  it("import (P2-06): amb VEC i PBL diferents, la capçalera també dona el PBL sense IVA (L'Escala)", () => {
    expect(amountMeta({ estimated_value: "245079.30", base_budget: null, base_budget_without_tax: "204232.75", currency: "EUR" })).toEqual({
      text: formatAmount("245079.30", "EUR"),
      note: "valor estimat del contracte (VEC)",
      budget: { text: formatAmount("204232.75", "EUR"), note: "pressupost base de licitació sense IVA" },
    });
  });

  it("zeros (19-10-2026): la capçalera salta els imports a 0,00 € i no en diu cap línia de PBL", () => {
    // VEC 0 i PBL > 0: el PBL sense IVA, amb la seva base.
    expect(amountMeta({ estimated_value: "0.00", base_budget: null, base_budget_without_tax: "30000.00", currency: "EUR" })).toEqual({
      text: formatAmount("30000.00", "EUR"),
      note: "pressupost base de licitació sense IVA",
      budget: null,
    });
    // VEC > 0 i PBL 0: el VEC, sense «PBL sense IVA: 0,00 €».
    expect(amountMeta({ estimated_value: "50000.00", base_budget: null, base_budget_without_tax: "0.00", currency: "EUR" }).budget).toBeNull();
    // Cap import útil: el mateix que sense import.
    expect(amountMeta({ estimated_value: "0.00", base_budget: null, base_budget_without_tax: "0.00", currency: "EUR" })).toEqual({ text: "No consta a les dades de la font", note: null, budget: null });
    expect(amountMeta({ estimated_value: null, base_budget: null, base_budget_without_tax: "0.00", currency: "EUR" })).toEqual({ text: "No consta a les dades de la font", note: null, budget: null });
  });

  it("zeros (19-10-2026): el fet del Resum diu que s'ha publicat a zero i que no s'utilitza", () => {
    const PLACSP = "Publicat a 0,00 € a la PLACSP (no s'utilitza com a import econòmic)";
    expect(amountFact("0.00", "EUR")).toEqual({ kind: "zero", text: PLACSP });
    expect(amountFact("0", "EUR")).toEqual({ kind: "zero", text: PLACSP });
    expect(amountFact("0.00", "EUR", { manual: true }).text).toBe("Entrat a 0,00 € (no s'utilitza com a import econòmic)");
    expect(amountFact(null, "EUR")).toEqual({ kind: "missing", text: "No consta a les dades de la font" });
    expect(amountFact("", "EUR")).toEqual({ kind: "missing", text: "No consta a les dades de la font" });
    expect(amountFact("204232.75", "EUR")).toEqual({ kind: "amount", text: formatAmount("204232.75", "EUR") });
    expect(amountFact("-5", "EUR").kind).toBe("not_used");
  });

  it("candidatura només si existeix; la decisió humana només si és real", () => {
    expect(candidatureMeta(null)).toBeNull();
    const ws = { status: "preparing_bid", human_decision: "go" } as WorkspaceDetail;
    expect(candidatureMeta(ws)).toEqual({ status: { level: "running", label: "Preparant oferta" }, decision: { level: "ready", label: "GO" } });
    const pending = { status: "awaiting_decision", human_decision: null } as WorkspaceDetail;
    expect(candidatureMeta(pending)).toEqual({ status: { level: "attention", label: "Pendent de decisió" }, decision: null });
  });

  it("l'estat «analysis» s'etiqueta segons l'anàlisi real del plec (A13): absent, obsoleta o en curs", () => {
    const coverage = (analysis_state: "absent" | "current" | "stale") =>
      ({ status: "analysis", human_decision: null, dossier_coverage: { analysis_state, mandatory_total: analysis_state === "absent" ? null : 3 } }) as unknown as WorkspaceDetail;
    expect(candidatureMeta(coverage("absent"))?.status.label).toBe("Pendent d'anàlisi");
    expect(candidatureMeta(coverage("stale"))?.status.label).toBe("Anàlisi obsoleta");
    expect(candidatureMeta(coverage("current"))?.status.label).toBe("En anàlisi");
  });
});
