import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Assessment, Compliance, ComplianceRun } from "@/lib/compliance";

const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post: (...args: unknown[]) => post(...args) } };
});

import { ComplianceSection } from "./ComplianceSection";

function run(overrides: Partial<ComplianceRun> = {}): ComplianceRun {
  return {
    id: "cr1",
    company_id: "c1",
    tender_id: "t1",
    status: "completed",
    is_current: true,
    rules_version: "rules-v1",
    company_profile_fingerprint: "a",
    requirements_fingerprint: "b",
    started_at: "2026-09-10T10:00:00Z",
    completed_at: "2026-09-10T10:01:00Z",
    duration_ms: 1000,
    requirements_total: 4,
    mandatory_total: 3,
    satisfies_count: 1,
    does_not_satisfy_count: 1,
    unknown_count: 1,
    needs_review_count: 1,
    not_applicable_count: 0,
    potential_blockers_count: 0,
    confirmed_blockers_count: 1,
    overall_result: "no_go",
    recommendation: "no_go",
    prefit_score: 92,
    error_code: null,
    error_message: null,
    ...overrides,
  };
}

function assessment(overrides: Partial<Assessment> = {}): Assessment {
  return {
    id: "a1",
    assessment_run_id: "cr1",
    requirement_id: "r1",
    tender_id: "t1",
    tender_lot_id: null,
    automatic_result: "satisfies",
    reviewed_result: null,
    effective_result: "satisfies",
    assessment_mode: "automatic",
    rule_code: "CERT_PRESENT_V1",
    explanation_code: "CERTIFICATION_FOUND_VALID",
    explanation_params: null,
    company_value_json: { name: "ISO 9001:2015" },
    requirement_value_json: { name: "ISO 9001" },
    obligation: "mandatory",
    blocker_status: "none",
    evidence_strength: "strong",
    review_status: "unreviewed",
    review_note: null,
    reviewed_at: null,
    reviewed_by: null,
    reviewed_by_name: null,
    calculated_at: "2026-09-10T10:01:00Z",
    evidence: [{ id: "ev1", entity_type: "company_certification", entity_id: "cert1", document_id: "doc1", label: "ISO 9001:2015 · AENOR", strength: "strong" }],
    requirement: { requirement_id: "r1", title: "Certificat ISO 9001 en vigor", category: "certification", review_status: "verified", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 4, quote: "Sistema de gestió de la qualitat certificat ISO 9001", document_name: "Plec administratiu" },
    ...overrides,
  };
}

const ASSESSMENTS: Assessment[] = [
  assessment(),
  assessment({ id: "a2", requirement_id: "r2", automatic_result: "does_not_satisfy", effective_result: "does_not_satisfy", blocker_status: "confirmed", rule_code: "CERT_PRESENT_V1", explanation_code: "CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE", company_value_json: null, evidence: [], evidence_strength: "missing", requirement: { requirement_id: "r2", title: "Certificat ISO 14001 en vigor", category: "certification", review_status: "verified", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 4, quote: null, document_name: "Plec administratiu" } }),
  assessment({ id: "a3", requirement_id: "r3", automatic_result: "unknown", effective_result: "unknown", explanation_code: "INSURANCE_DATA_INCOMPLETE", company_value_json: { sublimit_per_victim: null, weird: true }, requirement_value_json: { coverage_amount: 150000, currency: "EUR" }, evidence: [], evidence_strength: "missing", requirement: { requirement_id: "r3", title: "Sublímit per víctima de 150.000 €", category: "insurance", review_status: "unreviewed", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 3, quote: "sublímite por víctima de 150.000 euros", document_name: "Plec administratiu" } }),
  assessment({ id: "a4", requirement_id: "r4", automatic_result: "needs_review", reviewed_result: "satisfies", effective_result: "satisfies", assessment_mode: "review_required", explanation_code: "EXPERIENCE_SIMILARITY_REVIEW", review_status: "verified", review_note: "Els tres contractes són de xarxa municipal.", reviewed_at: "2026-09-12T09:00:00Z", reviewed_by: "u1", reviewed_by_name: "Anna Puig", requirement: { requirement_id: "r4", title: "Tres contractes similars", category: "experience", review_status: "verified", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 10, quote: null, document_name: "Plec administratiu" } }),
];

const compliance: Compliance = { run: run(), assessments: ASSESSMENTS, is_stale: false };
const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const reload = vi.fn(async () => undefined);

function renderSection(value: Compliance | null, extra: Partial<React.ComponentProps<typeof ComplianceSection>> = {}) {
  return render(
    <ComplianceSection
      tenderId="t1"
      companyId="c1"
      compliance={value}
      loading={false}
      error={null}
      prefitScore={92}
      analysisState="current"
      onReload={reload}
      {...extra}
    />,
  );
}

describe("ComplianceSection", () => {
  beforeEach(() => {
    post.mockReset();
    reload.mockClear();
  });

  it("indeterminat: bàner que diu que no s'ha avaluat res i comptadors apagats; mai verd", async () => {
    const { container } = renderSection({ run: run({ overall_result: "undetermined", recommendation: "undetermined", requirements_total: 0, mandatory_total: 0, satisfies_count: 0, does_not_satisfy_count: 0, unknown_count: 0, needs_review_count: 0, confirmed_blockers_count: 0 }), assessments: [], is_stale: false });
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("Encara no es pot dir si l'empresa compleix");
    const counters = container.querySelector("[data-compliance-counters]")!;
    expect(counters.className).toContain("opacity-55");
    const eligibility = [...container.querySelectorAll("[data-level]")].find((p) => p.textContent?.includes("Sense determinar"))!;
    expect(eligibility.getAttribute("data-level")).toBe("neutral");
  });

  it("amb avaluacions: primer el que bloqueja, «Sense dades» en atenció (no un no), i la recomanació és automàtica", async () => {
    const { container } = renderSection(compliance);
    const groups = [...container.querySelectorAll("[data-assessment-group]")].map((g) => g.getAttribute("data-assessment-group"));
    expect(groups.slice(0, 2)).toEqual(["blockers", "unknown"]);
    const blocker = screen.getByText("Certificat ISO 14001 en vigor").closest("li")!;
    const pills = (row: Element) => [...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pills(blocker)).toContain("No compleix[blocking]");
    expect(pills(blocker)).toContain("Bloqueig confirmat[blocking]");
    expect(blocker.querySelector("[data-kind='rule']")).not.toBeNull();
    const unknown = screen.getByText("Sublímit per víctima de 150.000 €").closest("li")!;
    expect(pills(unknown)).toContain("Sense dades[attention]");
    expect(unknown.textContent).toContain("Valor no comparable automàticament");
    expect(unknown.querySelector("[title*='sublimit_per_victim']")).not.toBeNull();
    expect(unknown.textContent).not.toContain('{"');
    // B1: cap verb d'onboarding; la sortida d'una dada que falta és el coneixement de l'empresa.
    expect(within(unknown).getByRole("link", { name: /Afegeix la dada al perfil/ }).getAttribute("href")).toBe("/empresa/coneixement");
    expect(container.textContent).not.toMatch(/Completa el perfil/);
    expect(container.textContent).not.toContain("CERT_PRESENT_V1");
    // A14: cap «Recomanació»; el que surt del motor és el resultat de l'anàlisi.
    expect(container.textContent).toContain("Resultat de l'anàlisi");
    expect(container.textContent).not.toMatch(/Recomanaci/);
    expect(container.textContent).not.toMatch(/Decisió/);
  });

  it("sense anàlisi del plec vigent no s'ofereix calcular el compliment (A7); amb anàlisi obsoleta, tampoc recalcular", async () => {
    const { container, unmount } = renderSection(null, { analysisState: "absent" });
    expect(screen.queryByRole("button", { name: /Analitza compliment/ })).toBeNull();
    expect(container.textContent).toContain("Cal una anàlisi del plec vigent");
    unmount();
    renderSection(compliance, { analysisState: "stale" });
    expect(screen.queryByRole("button", { name: "Recalcula" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Analitza compliment/ })).toBeNull();
  });

  it("una revisió humana va signada, amb el resultat automàtic original a sota", async () => {
    const { container } = renderSection(compliance);
    // Els acreditats van plegats sota el que demana atenció: es despleguen.
    fireEvent.click(screen.getByRole("button", { name: /plegats · desplega/ }));
    const reviewed = screen.getByText("Tres contractes similars").closest("li")!;
    const human = reviewed.querySelector("[data-kind='human']")!;
    expect(human).not.toBeNull();
    expect(reviewed.textContent).toContain("Anna Puig");
    expect(reviewed.textContent).toContain("Els tres contractes són de xarxa municipal.");
    expect(reviewed.textContent).toContain("Resultat automàtic");
    expect(reviewed.textContent).toContain("Cal revisar");
    expect(reviewed.querySelector("[data-kind='rule']")).not.toBeNull();
    expect(container.querySelectorAll("[data-kind='human']").length).toBe(1);
  });

  it("l'evidència del plec surt amb EvidenceRef i la de l'empresa amb la seva força", async () => {
    renderSection(compliance);
    fireEvent.click(screen.getByRole("button", { name: /plegats · desplega/ }));
    const row = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(row.textContent).toContain("Plec administratiu · p. 4 · versió del text 1");
    expect([...row.querySelectorAll("a")].some((a) => a.getAttribute("href") === "/licitacions/t1/documents/d1?page=4&extraction=ex1")).toBe(true);
    expect(row.textContent).toContain("ISO 9001:2015 · AENOR");
    expect(row.textContent).toContain("Acreditada");
  });

  it("contradir el motor demana motiu i fa el POST de sempre; confirmar no en demana", async () => {
    post.mockResolvedValue({});
    renderSection(compliance);
    const row = screen.getByText("Sublímit per víctima de 150.000 €").closest("li")!;
    fireEvent.click([...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Marca compleix")!);
    const save = [...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Desa la revisió")! as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(row.querySelector("textarea")!, { target: { value: "La pòlissa detalla el sublímit." } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/assessments/a3/review", { review_status: "verified", reviewed_result: "satisfies", note: "La pòlissa detalla el sublímit." });
    expect(reload).toHaveBeenCalled();
  });

  it("recalcular fa el POST de compliment amb force i el bàner d'obsolet ho ofereix", async () => {
    post.mockResolvedValue({});
    renderSection({ ...compliance, is_stale: true });
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("han canviat");
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/tenders/t1/compliance", { force: true });
  });

  it("l'etiqueta d'una evidència del backend no ensenya cap codi cru", async () => {
    renderSection({
      ...compliance,
      assessments: [
        assessment({
          evidence: [{ id: "ev9", entity_type: "company_insurance", entity_id: "ins1", document_id: null, label: "civil_liability: unknown 1000000.00 EUR", strength: "strong" }],
        }),
      ],
    });
    // Una sola avaluació: el grup d'acreditats no va plegat.
    const row = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(row.textContent).not.toContain("civil_liability");
    expect(row.textContent).toContain("Responsabilitat civil");
    expect(row.querySelector("[title='civil_liability: unknown 1000000.00 EUR']")).not.toBeNull();
  });
});

describe("ComplianceSection · duplicats d'una mateixa exigència", () => {
  beforeEach(() => {
    post.mockReset();
    reload.mockClear();
  });

  const econ = (id: string, requirementId: string, title: string, page: number, quote: string, canonical: boolean) =>
    assessment({
      id,
      requirement_id: requirementId,
      automatic_result: "unknown",
      effective_result: "unknown",
      rule_code: "economic_turnover_aggregate_v2",
      explanation_code: "ECONOMIC_DATA_INCOMPLETE",
      company_value_json: null,
      requirement_value_json: { amount: "91.904,74 €" },
      evidence: [],
      evidence_strength: "missing",
      consolidation: { canonical_requirement_id: "rA", is_canonical: canonical, member_requirement_ids: ["rA", "rB"], divergent: false },
      requirement: {
        requirement_id: requirementId,
        title,
        category: "economic_solvency",
        review_status: "unreviewed",
        tender_document_id: "d1",
        extraction_id: "ex1",
        extraction_revision: 1,
        page_number: page,
        quote,
        document_name: "Plec administratiu",
        citations: [{ evidence_id: `e-${id}`, tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: page, quote, document_name: "Plec administratiu" }],
      },
    });

  const grouped: Compliance = {
    run: run({ requirements_total: 1, unknown_count: 1, satisfies_count: 0, does_not_satisfy_count: 0, needs_review_count: 0, confirmed_blockers_count: 0, overall_result: "review_required", recommendation: "review" }),
    assessments: [
      econ("a1", "rA", "Solvència econòmica: volum anual de negoci", 3, "Volum anual de negoci del millor dels 3 últims exercicis", true),
      econ("a2", "rB", "Volum anual de negocis mínim", 6, "per un import igual o superior a 91.904,74 euros", false),
    ],
    groups: [{ canonical_assessment_id: "a1", canonical_requirement_id: "rA", member_assessment_ids: ["a1", "a2"], member_requirement_ids: ["rA", "rB"], effective_result: "unknown", blocker_status: "none", divergent: false }],
    is_stale: false,
  };

  it("una fila per exigència, amb «també a» i totes les cites dels duplicats", () => {
    const { container } = renderSection(grouped);
    expect(container.textContent).toContain("Requisits avaluats (1)");
    expect(screen.queryByText("Volum anual de negocis mínim")).toBeNull();
    const row = screen.getByText("Solvència econòmica: volum anual de negoci").closest("li")!;
    const details = row.querySelector("[data-group-citations]")!;
    expect(details.textContent).toContain("També a 1 requisit més · 1 cita addicional");
    expect(details.textContent).toContain("També a: Volum anual de negocis mínim");
    expect(details.textContent).toContain("per un import igual o superior a 91.904,74 euros");
    expect(row.textContent).toContain("Volum anual de negoci del millor dels 3 últims exercicis");
  });

  it("el resultat de la fila és el del grup, i un grup divergent ho diu", () => {
    renderSection({
      ...grouped,
      groups: [{ ...grouped.groups![0], effective_result: "does_not_satisfy", blocker_status: "potential", divergent: true }],
    });
    const row = screen.getByText("Solvència econòmica: volum anual de negoci").closest("li")!;
    const pills = [...row.querySelectorAll("[data-level]")].map((p) => p.textContent?.trim());
    expect(pills).toContain("No compleix");
    expect(pills).toContain("Possible bloqueig");
    expect(row.querySelector("[data-group-divergent]")).not.toBeNull();
  });

  it("revisar una exigència revisa tots els seus requisits", async () => {
    post.mockResolvedValue({});
    renderSection(grouped);
    const row = screen.getByText("Solvència econòmica: volum anual de negoci").closest("li")!;
    fireEvent.click([...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Confirma")!);
    fireEvent.click([...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Desa la revisió")!);
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenNthCalledWith(1, "/companies/c1/assessments/a1/review", { review_status: "verified", reviewed_result: null, note: null });
    expect(post).toHaveBeenNthCalledWith(2, "/companies/c1/assessments/a2/review", { review_status: "verified", reviewed_result: null, note: null });
    expect(reload).toHaveBeenCalled();
  });

  it("sense grups de l'API, cada avaluació és una fila (com abans)", () => {
    const { container } = renderSection({ ...grouped, groups: undefined });
    expect(container.textContent).toContain("Requisits avaluats (2)");
  });
});

describe("ComplianceSection · el valor que la regla ha comparat (I6)", () => {
  beforeEach(() => {
    post.mockReset();
    reload.mockClear();
  });

  const citation = (id: string, page: number, quote: string) => ({
    evidence_id: id, tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: page, quote, document_name: "Plec administratiu",
  });

  function decided(overrides: Partial<Assessment>): Assessment {
    return assessment({
      automatic_result: "does_not_satisfy",
      effective_result: "does_not_satisfy",
      blocker_status: "potential",
      assessment_mode: "automatic",
      evidence: [],
      evidence_strength: "missing",
      ...overrides,
    });
  }

  it("RC patronal per víctima: el llindar normalitzat, la procedència i el valor de l'empresa", () => {
    const row = decided({
      id: "p1",
      rule_code: "insurance_limits_v2",
      explanation_code: "INSURANCE_LIMIT_BELOW",
      requirement_value_json: { amount: "150.000 €", unit: "per víctima" },
      compared_value: { kind: "insurance", insurance_type: "civil_liability", limits: [{ kind: "employer_per_victim", amount: "150000", currency: "EUR" }] },
      compared_provenance: [{ field: "limits.amount", source: "value_json", raw: "150.000 €" }],
      normalization_status: "normalized",
      company_value_json: { type: "civil_liability", limits: [{ kind: "per_claim", amount: 200000, currency: "EUR" }, { kind: "employer_per_victim", amount: 100000, currency: "EUR" }] },
      requirement: { requirement_id: "r1", title: "Sublímit RC patronal mínim 150.000 € per víctima", category: "insurance", review_status: "unreviewed", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 8, quote: "sublímit mínim de 150.000 € per víctima", document_name: "Plec administratiu", citations: [citation("e1", 8, "sublímit mínim de 150.000 € per víctima")] },
    });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Sublímit RC patronal mínim 150.000 € per víctima").closest("li")!;
    expect(li.textContent).toMatch(/150\.000,00\s€ RC patronal per víctima · del valor extret/);
    expect(li.textContent).toMatch(/100\.000,00\s€ RC patronal per víctima/);
    expect(li.textContent).not.toContain("Valor no comparable");
    expect(li.textContent).toContain("No compleix");
  });

  it("RC per sinistre i any llegida de la cita: diu la pàgina", () => {
    const row = decided({
      id: "p2",
      automatic_result: "satisfies",
      effective_result: "satisfies",
      blocker_status: "none",
      rule_code: "insurance_limits_v2",
      explanation_code: "INSURANCE_LIMITS_MET",
      requirement_value_json: null,
      compared_value: { kind: "insurance", limits: [{ kind: "per_claim", amount: "300000", currency: "EUR" }, { kind: "annual_aggregate", amount: "300000", currency: "EUR" }] },
      compared_provenance: [{ field: "limits.amount", source: "quote", raw: "cobertura mínima de 300.000 euros", evidence_id: "e17" }],
      company_value_json: { type: "civil_liability", limits: [{ kind: "per_claim", amount: 600000, currency: "EUR" }, { kind: "annual_aggregate", amount: 600000, currency: "EUR" }] },
      requirement: { requirement_id: "r2", title: "Pòlissa de RC: 300.000 euros per sinistre i any", category: "insurance", review_status: "unreviewed", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 17, quote: "cobertura mínima de 300.000 euros", document_name: "Plec administratiu", citations: [citation("e17", 17, "cobertura mínima de 300.000 euros per sinistre i any")] },
    });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Pòlissa de RC: 300.000 euros per sinistre i any").closest("li")!;
    expect(li.textContent).toMatch(/300\.000,00\s€ per sinistre · 300\.000,00\s€ agregat anual · de la cita, p\. 17/);
    expect(li.textContent).not.toContain("Valor no comparable");
  });

  it("solvència econòmica: llindar, període i la facturació de l'empresa", () => {
    const row = decided({
      id: "p3",
      rule_code: "economic_turnover_aggregate_v2",
      explanation_code: "ECONOMIC_THRESHOLD_NOT_MET",
      requirement_value_json: { amount: "91.904,74 €", period: "millor dels 3 últims exercicis" },
      compared_value: { kind: "economic_solvency", threshold: { amount: "91904.74", currency: "EUR" }, reference_period: { type: "last_n_years", years: 3 }, aggregation: "max" },
      compared_provenance: [{ field: "threshold.amount", source: "value_json", raw: "91.904,74 €" }],
      company_value_json: { amount: 71000, currency: "EUR", aggregation: "max" },
      requirement: { requirement_id: "r3", title: "Solvència econòmica: volum anual de negoci", category: "economic_solvency", review_status: "unreviewed", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 3, quote: null, document_name: "Plec administratiu", citations: [] },
    });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Solvència econòmica: volum anual de negoci").closest("li")!;
    expect(li.textContent).toMatch(/≥ 91\.904,74\s€ · millor exercici dels 3 últims · del valor extret/);
    expect(li.textContent).toContain("71.000 EUR");
    expect(li.textContent).not.toContain("Valor no comparable");
  });

  it("una fila decidida per la regla mai no diu «Valor no comparable»", () => {
    const row = decided({ id: "p4", requirement_value_json: { weird: true }, company_value_json: { odd: 1 } });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(li.textContent).not.toContain("Valor no comparable");
    expect(li.textContent).toContain("Comparat per la regla");
  });

  it("un grup divergent ensenya l'explicació del membre que decideix i no ofereix «Confirma»", () => {
    const canonical = assessment({ id: "g1", requirement_id: "rA", automatic_result: "satisfies", effective_result: "satisfies", explanation_code: "ECONOMIC_THRESHOLD_MET", evidence: [] });
    const member = assessment({ id: "g2", requirement_id: "rB", automatic_result: "satisfies", reviewed_result: "does_not_satisfy", effective_result: "does_not_satisfy", explanation_code: "ECONOMIC_THRESHOLD_NOT_MET", evidence: [], requirement: { requirement_id: "rB", title: "Volum anual de negocis mínim", category: "economic_solvency", review_status: "unreviewed", tender_document_id: null, extraction_id: null, extraction_revision: null, page_number: null, quote: null, document_name: null, citations: [] } });
    renderSection({
      run: run(),
      assessments: [canonical, member],
      groups: [{ canonical_assessment_id: "g1", canonical_requirement_id: "rA", member_assessment_ids: ["g1", "g2"], member_requirement_ids: ["rA", "rB"], effective_result: "does_not_satisfy", blocker_status: "potential", divergent: true }],
      is_stale: false,
    });
    const li = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(li.textContent).toContain("La facturació registrada no arriba al llindar exigit.");
    expect([...li.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Confirma")).toBe(false);
  });
});

describe("ComplianceSection · ronda 2", () => {
  const ensRow = (compared: Record<string, unknown>, overrides: Partial<Assessment> = {}) =>
    assessment({
      id: "e1",
      automatic_result: "does_not_satisfy",
      effective_result: "does_not_satisfy",
      blocker_status: "potential",
      assessment_mode: "automatic",
      rule_code: "certification_exact_v1",
      explanation_code: "CERTIFICATION_LEVEL_BELOW",
      requirement_value_json: null,
      company_value_json: null,
      evidence: [],
      compared_value: compared,
      compared_provenance: [{ field: "certification.level", source: "quote", raw: "ENS de categoria ALTA", evidence_id: "q1" }],
      requirement: { requirement_id: "r1", title: "Aplicació de l'ENS", category: "technical_requirement", review_status: "unreviewed", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 9, quote: "ENS de categoria ALTA", document_name: "Plec administratiu", citations: [{ evidence_id: "q1", tender_document_id: "d1", extraction_id: "ex1", extraction_revision: 1, page_number: 9, quote: "ENS de categoria ALTA", document_name: "Plec administratiu" }] },
      ...overrides,
    });

  it("una fila d'ENS diu la categoria exigida i d'on surt", () => {
    renderSection({ run: run(), assessments: [ensRow({ kind: "ens", level: "alta", or_higher: false })], is_stale: false });
    const li = screen.getByText("Aplicació de l'ENS").closest("li")!;
    expect(li.textContent).toContain("ENS categoria alta · de la cita, p. 9");
    expect(li.textContent).not.toContain("Valor no comparable");
  });

  it("si el plec no diu la categoria, la fila ho diu", () => {
    const row = ensRow(
      { kind: "ens", level: null, or_higher: false },
      { automatic_result: "needs_review", effective_result: "needs_review", blocker_status: "none", assessment_mode: "review_required", explanation_code: "CERTIFICATION_LEVEL_NOT_STATED", compared_provenance: [] },
    );
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Aplicació de l'ENS").closest("li")!;
    expect(li.textContent).toContain("ENS · el plec no indica la categoria");
  });

  it("una fila revisada a mà amb veredicte no diu «Valor no comparable»", () => {
    const row = assessment({
      id: "m1",
      automatic_result: "needs_review",
      reviewed_result: "does_not_satisfy",
      effective_result: "does_not_satisfy",
      assessment_mode: "review_required",
      review_status: "verified",
      reviewed_by_name: "Anna Puig",
      review_note: "No consta la pòlissa.",
      requirement_value_json: { weird: true },
      company_value_json: { odd: 1 },
      evidence: [],
    });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(li.textContent).not.toContain("Valor no comparable");
    expect(li.textContent).toContain("No compleix");
    // Ronda 3 (M2-2): la regla no ha comparat res; el veredicte és d'una persona.
    expect(li.textContent).not.toContain("Comparat per la regla");
    expect(li.textContent).toContain("Revisat per una persona");
  });

  it("una fila decidida per la regla i després revisada continua dient que la regla ha comparat", () => {
    const row = assessment({
      id: "m2",
      automatic_result: "satisfies",
      reviewed_result: "does_not_satisfy",
      effective_result: "does_not_satisfy",
      assessment_mode: "automatic",
      review_status: "verified",
      reviewed_by_name: "Anna Puig",
      review_note: "La pòlissa ha caducat.",
      requirement_value_json: { weird: true },
      company_value_json: { odd: 1 },
      evidence: [],
    });
    renderSection({ run: run(), assessments: [row], is_stale: false });
    const li = screen.getByText("Certificat ISO 9001 en vigor").closest("li")!;
    expect(li.textContent).toContain("Comparat per la regla");
    expect(li.textContent).not.toContain("Valor no comparable");
  });
});

describe("ComplianceSection · Pas 2: confirmats, possibles i revisions conservades", () => {
  const groupOfRow = (title: string) =>
    screen.getByText(title).closest("[data-assessment-group]")?.getAttribute("data-assessment-group");

  it("un possible bloqueig va a «Possibles bloquejos», un de confirmat a «Bloquejos confirmats» i un no compleix que no bloqueja al seu grup", () => {
    const value: Compliance = {
      run: run({ confirmed_blockers_count: 1, potential_blockers_count: 1 }),
      is_stale: false,
      assessments: [
        assessment({ id: "c1", requirement_id: "rc", automatic_result: "does_not_satisfy", effective_result: "does_not_satisfy", blocker_status: "confirmed", requirement: { ...assessment().requirement!, requirement_id: "rc", title: "Confirmat pel revisor" } }),
        assessment({ id: "p1", requirement_id: "rp", automatic_result: "does_not_satisfy", effective_result: "does_not_satisfy", blocker_status: "potential", requirement: { ...assessment().requirement!, requirement_id: "rp", title: "Possible pel motor" } }),
        assessment({ id: "n1", requirement_id: "rn", obligation: "scored", automatic_result: "does_not_satisfy", effective_result: "does_not_satisfy", blocker_status: "none", requirement: { ...assessment().requirement!, requirement_id: "rn", title: "No obligatori que no es compleix" } }),
      ],
    };
    const { container } = renderSection(value);
    expect(groupOfRow("Confirmat pel revisor")).toBe("blockers");
    expect(groupOfRow("Possible pel motor")).toBe("potential");
    expect(groupOfRow("No obligatori que no es compleix")).toBe("not_met");
    expect(container.textContent).toContain("Bloquejos confirmats · 1");
    expect(container.textContent).toContain("Possibles bloquejos · 1");
    expect(container.textContent).not.toContain("Bloquegen");
  });

  it("una revisió conservada d'un càlcul anterior avisa si el resultat automàtic ha canviat", () => {
    const value: Compliance = {
      run: run(),
      is_stale: false,
      assessments: [
        assessment({
          automatic_result: "satisfies",
          reviewed_result: "does_not_satisfy",
          effective_result: "does_not_satisfy",
          review_status: "verified",
          review_note: "Revisat a mà",
          explanation_params: { review_carried_over: { from_run: "cr0", reviewed_against: "unknown", automatic_changed: true } },
        }),
      ],
    };
    const { container } = renderSection(value);
    const notice = container.querySelector("[data-carried-review-changed]");
    expect(notice?.textContent).toContain("llavors: Sense dades");
  });

  it("una revisió conservada sense canvi del resultat automàtic no avisa de res", () => {
    const value: Compliance = {
      run: run(),
      is_stale: false,
      assessments: [
        assessment({
          reviewed_result: "satisfies",
          review_status: "verified",
          review_note: "Revisat a mà",
          explanation_params: { review_carried_over: { from_run: "cr0", reviewed_against: "satisfies", automatic_changed: false } },
        }),
      ],
    };
    const { container } = renderSection(value);
    expect(container.querySelector("[data-carried-review-changed]")).toBeNull();
    expect(container.querySelector("[data-review-not-carried]")).toBeNull();
  });

  it("una confirmació que no s'ha conservat ho diu a la fila, que torna a estar sense revisar", () => {
    const value: Compliance = {
      run: run({ confirmed_blockers_count: 0, potential_blockers_count: 1 }),
      is_stale: false,
      assessments: [
        assessment({
          automatic_result: "does_not_satisfy",
          effective_result: "does_not_satisfy",
          blocker_status: "potential",
          explanation_params: {
            review_not_carried: { from_run: "cr0", reviewed_against: "satisfies", reviewed_result: null, reason: "automatic_changed" },
          },
        }),
      ],
    };
    const { container } = renderSection(value);
    const notice = container.querySelector("[data-review-not-carried]");
    expect(notice?.getAttribute("data-review-not-carried")).toBe("automatic_changed");
    expect(notice?.textContent).toContain("«Compleix»");
    expect(notice?.textContent).toContain("no s'ha conservat");
    // Ningú no ha confirmat l'incompliment: és un possible bloqueig, i la fila és de la regla.
    expect(groupOfRow("Certificat ISO 9001 en vigor")).toBe("potential");
  });

  it("la revisió d'un requisit que s'ha editat no es conserva, i es diu", () => {
    const value: Compliance = {
      run: run(),
      is_stale: false,
      assessments: [
        assessment({
          explanation_params: {
            review_not_carried: { from_run: "cr0", reviewed_against: "unknown", reviewed_result: "satisfies", reason: "requirement_changed" },
          },
        }),
      ],
    };
    const { container } = renderSection(value);
    const notice = container.querySelector("[data-review-not-carried]");
    expect(notice?.getAttribute("data-review-not-carried")).toBe("requirement_changed");
    expect(notice?.textContent).toContain("El requisit ha canviat");
  });

  it("en un grup de duplicats, l'avís d'un membre que no és el canònic també surt a la fila", () => {
    const econ = (id: string, requirementId: string, canonical: boolean, params: Record<string, unknown> | null) =>
      assessment({
        id,
        requirement_id: requirementId,
        automatic_result: "does_not_satisfy",
        reviewed_result: "satisfies",
        effective_result: "satisfies",
        review_status: "verified",
        review_note: "Comptes dipositats",
        explanation_params: params,
        consolidation: { canonical_requirement_id: "rA", is_canonical: canonical, member_requirement_ids: ["rA", "rB"], divergent: false },
        requirement: { ...assessment().requirement!, requirement_id: requirementId, title: canonical ? "Volum anual de negoci" : "Volum mínim" },
      });
    const value: Compliance = {
      run: run({ requirements_total: 1 }),
      is_stale: false,
      assessments: [
        econ("a1", "rA", true, { review_carried_over: { from_run: "cr0", reviewed_against: "does_not_satisfy", automatic_changed: false } }),
        econ("a2", "rB", false, { review_carried_over: { from_run: "cr0", reviewed_against: "unknown", automatic_changed: true } }),
      ],
      groups: [{ canonical_assessment_id: "a1", canonical_requirement_id: "rA", member_assessment_ids: ["a1", "a2"], member_requirement_ids: ["rA", "rB"], effective_result: "satisfies", blocker_status: "none", divergent: false }],
    };
    const { container } = renderSection(value);
    expect(container.querySelector("[data-carried-review-changed]")?.textContent).toContain("llavors: Sense dades");
  });
});
