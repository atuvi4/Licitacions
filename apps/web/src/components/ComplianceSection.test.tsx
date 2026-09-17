import { act, fireEvent, render, screen } from "@testing-library/react";
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
    <ComplianceSection tenderId="t1" companyId="c1" compliance={value} loading={false} error={null} prefitScore={92} onReload={reload} {...extra} />,
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
    expect(container.textContent).not.toContain("CERT_PRESENT_V1");
    expect(container.textContent).toContain("Recomanació automàtica");
    expect(container.textContent).not.toMatch(/Decisió/);
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
    expect(row.textContent).toContain("Plec administratiu · p. 4 · revisió 1");
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
});
