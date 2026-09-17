import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TenderDetail } from "@/lib/tenders";

const get = vi.fn();
const post = vi.fn();
const push = vi.fn();
const replace = vi.fn();
let search = "";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) } };
});
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1" }) }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ tenderId: "t1" }),
  useRouter: () => ({ push, replace, refresh: vi.fn() }),
  usePathname: () => "/licitacions/t1",
  useSearchParams: () => new URLSearchParams(search),
}));
vi.mock("@/lib/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/jobs")>();
  return { ...actual, useJobRequest: () => ({ request: null, active: false, track: vi.fn() }), requestProcessing: vi.fn() };
});
vi.mock("@/components/ComplianceSection", () => ({ ComplianceSection: () => <div data-testid="compliance">Compliment (secció actual)</div> }));
vi.mock("@/components/RequirementAnalysis", () => ({ RequirementAnalysisSection: () => <div data-testid="analysis">Anàlisi del plec (secció actual)</div> }));
vi.mock("@/components/TenderChangeHistory", () => ({ TenderChangeHistory: () => <div data-testid="history">Historial de canvis (secció actual)</div> }));

import { ApiError } from "@/lib/api";

import TenderDetailPage from "./page";

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void };

const detail: TenderDetail = {
  id: "t1",
  external_id: "ext-1",
  expediente_number: "EXP-1",
  title: "Desplegament de xarxa de fibra òptica",
  description: "Objecte del contracte.",
  status: "open",
  source_status_code: null,
  procedure_type: "open",
  contract_type: "services",
  estimated_value: "240000",
  base_budget: "200000",
  base_budget_without_tax: null,
  currency: "EUR",
  publication_date: "2026-09-02",
  deadline_at: new Date(Date.now() + 6 * 24 * 60 * 60 * 1000).toISOString(),
  place_of_execution: "Girona",
  nuts_code: "ES512",
  country_code: "ES",
  source_url: "https://example.org/exp-1",
  first_seen_at: "2026-09-02T00:00:00Z",
  last_seen_at: "2026-09-10T00:00:00Z",
  last_changed_at: null,
  source_publication_count: 2,
  buyer: { id: "b", name: "Ajuntament Demo", tax_identifier: null, city: "Girona", nuts_code: null, postal_code: null, hierarchy: null, buyer_profile_url: null } as TenderDetail["buyer"],
  cpvs: [{ cpv_code: "32412100", description: "Xarxa de telecomunicacions", is_main: true }] as TenderDetail["cpvs"],
  lots: [],
  documents: [
    { id: "d1", name: "Plec administratiu.pdf", document_type: "administrative", url: "https://example.org/d1", latest_file: null },
    { id: "d2", name: "Plec tècnic.pdf", document_type: "technical", url: "https://example.org/d2", latest_file: { file_id: "f2", version_number: 1, download_status: "failed", file_size: null, sha256: null, extraction_status: null, quality: null, page_count: null, text_char_count: null, ocr_candidate: false, extraction_id: null, processed_at: null, error_code: "download_failed" } },
    { id: "d3", name: "Annex.pdf", document_type: "other", url: "https://example.org/d3", latest_file: null },
  ] as unknown as TenderDetail["documents"],
  traceability: null,
};

const match = { id: "m1", tender_id: "t1", score: 92, status: "candidate", exclusion_kind: null, exclusion_reason_code: null, cpv_score: 40, geographic_score: 25, economic_score: 25, timing_score: 2, data_coverage: 1, factors: [{ dimension: "cpv", result: "match", reason_code: "CPV_EXACT_HIGH_PRIORITY", weight: 40, score: 40, max_score: 40, company_value: null, source_value: null, detail: {} }], rules_version: "v1", calculated_at: "2026-09-15T00:00:00Z", best_lot_id: null, candidate_lot_count: 0, total_lot_count: 0, is_stale: false, lot_matches: [] };
const analysis = { run: { id: "x1", status: "completed", started_at: "2026-09-09T10:00:00Z", completed_at: "2026-09-09T10:05:00Z", requirements_extracted: 14, skipped_documents: [] }, requirements: [], award_criteria: [], is_stale: false };
const complianceRun = { id: "r1", company_id: "c1", tender_id: "t1", status: "completed", is_current: true, rules_version: "v1", company_profile_fingerprint: "a", requirements_fingerprint: "b", started_at: "2026-09-10T10:00:00Z", completed_at: "2026-09-10T10:01:00Z", duration_ms: 1, requirements_total: 14, mandatory_total: 10, satisfies_count: 9, does_not_satisfy_count: 2, unknown_count: 0, needs_review_count: 3, not_applicable_count: 0, potential_blockers_count: 0, confirmed_blockers_count: 2, overall_result: "no_go", recommendation: "no_go", prefit_score: 92, error_code: null, error_message: null };
const workspace = { id: "w1", status: "preparing_bid", human_decision: "go", tender_id: "t1" };

function respondWith(map: Record<string, unknown | Deferred | Error>) {
  get.mockImplementation((path: string) => {
    const key = path in map ? path : Object.keys(map).find((prefix) => path.startsWith(prefix));
    const value = key ? map[key] : undefined;
    if (value instanceof Error) return Promise.reject(value);
    if (value && typeof value === "object" && "resolve" in (value as object)) {
      return new Promise((resolve, reject) => {
        (value as Deferred).resolve = resolve;
        (value as Deferred).reject = reject;
      });
    }
    return Promise.resolve(value);
  });
}

function base(extra: Record<string, unknown> = {}) {
  return {
    "/tenders/t1/revisions": [],
    "/tenders/t1/history": [],
    "/tenders/t1/analysis/requirements": analysis,
    "/tenders/t1": detail,
    "/companies/c1/matches/by-tender/t1": match,
    "/companies/c1/tenders/t1/compliance": { run: complianceRun, assessments: [], is_stale: false },
    "/companies/c1/tenders/t1/workspace": workspace,
    "/companies/c1/analysis-limits": { can_process_documents: true, can_confirm: true, active_now: 0 },
    ...extra,
  };
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const notFound = () => new ApiError(404, "not_found", "No trobat");

describe("Fitxa de licitació", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    push.mockReset();
    replace.mockReset();
    search = "";
  });

  it("mentre carrega pinta l'esquelet de detall amb context real", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith(base({ "/tenders/t1": pending }));
    const { container } = render(<TenderDetailPage />);
    await flush();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("carregant la licitació");
  });

  it("capçalera persistent amb dades reals, candidatura existent i pestanyes amb recompte; Resum per defecte", async () => {
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { level: 1, name: "Desplegament de xarxa de fibra òptica" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Oportunitats" }).getAttribute("href")).toBe("/licitacions");
    expect(container.textContent).toContain("Ajuntament Demo · EXP-1");

    const header = container.querySelector("[data-tender-header]")!;
    const pills = [...header.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pills).toContain("6 dies[blocking]");
    expect(pills).toContain("En termini[ready]");
    expect(pills).toContain("Preparant oferta[running]");
    expect(pills).toContain("GO[ready]");
    expect(header.querySelector("[data-score-tone]")?.textContent).toBe("92");
    expect(header.textContent).toContain("2 bloquegen · 3 per revisar");
    expect(header.textContent).toContain("240.000");
    expect(header.textContent).toContain("Publicada 2 cops");
    expect(header.textContent).not.toMatch(/probabilitat/i);
    expect(screen.getByRole("button", { name: "Continua la candidatura" })).toBeTruthy();

    const tabs = screen.getAllByRole("tab").map((tab) => `${tab.textContent?.replace(/\s+/g, " ").trim()}${tab.getAttribute("aria-selected") === "true" ? " *" : ""}`);
    expect(tabs).toEqual(["Resum *", "Plecs3", "Requisits14", "Historial"]);

    // Resum: pre-fit i dades; els plecs i els requisits no hi són.
    expect(screen.getByText("Pre-fit amb el vostre perfil")).toBeTruthy();
    expect(screen.getByText("Objecte del contracte.")).toBeTruthy();
    expect(screen.queryByText("Plec administratiu.pdf")).toBeNull();
    expect(screen.queryByTestId("compliance")).toBeNull();
  });

  it("?tab=requisits obre Requisits amb l'anàlisi i el compliment actuals; canviar de pestanya escriu la URL", async () => {
    search = "tab=requisits";
    respondWith(base());
    render(<TenderDetailPage />);
    expect(await screen.findByTestId("compliance")).toBeTruthy();
    expect(screen.getByTestId("analysis")).toBeTruthy();
    expect(screen.queryByText("Objecte del contracte.")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Plecs/ }));
    expect(replace).toHaveBeenCalledWith("/licitacions/t1?tab=plecs", { scroll: false });
    expect(await screen.findByText("Plec administratiu.pdf")).toBeTruthy();
    expect(screen.queryByTestId("compliance")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /Historial/ }));
    expect(await screen.findByTestId("history")).toBeTruthy();
    expect(screen.getByText("Publicacions de la font")).toBeTruthy();
  });

  it("Plecs conserva el processat i no mostra codis interns com a text", async () => {
    search = "tab=plecs";
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    expect(container.textContent).not.toContain("download_failed");
    expect(container.querySelector("[title='download_failed']")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: /Processa|Reprocessa/ }).length).toBeGreaterThanOrEqual(3);
  });

  it("sense candidatura ni pre-fit: no simula res i ofereix les sortides reals", async () => {
    respondWith(base({ "/companies/c1/tenders/t1/workspace": notFound(), "/companies/c1/matches/by-tender/t1": notFound(), "/companies/c1/tenders/t1/compliance": { run: { ...complianceRun, overall_result: "undetermined", confirmed_blockers_count: 0 }, assessments: [], is_stale: false } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Sense candidatura");
    expect(header.textContent).toContain("encara no avaluats");
    expect(header.querySelector("[data-score-tone]")).toBeNull();
    expect(header.textContent).not.toContain("GO");
    expect(await screen.findByRole("button", { name: "Obre candidatura" })).toBeTruthy();
    expect(screen.getByText("Aquesta licitació encara no té pre-fit calculat.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Recalcula el pre-fit/ }).getAttribute("href")).toBe("/licitacions");
  });

  it("licitació anul·lada: bàner de bloqueig i cap acció d'obrir candidatura", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, status: "cancelled" }, "/companies/c1/tenders/t1/workspace": notFound() }));
    render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByRole("status").textContent).toContain("anul·lat");
    await flush();
    expect(screen.queryByRole("button", { name: "Obre candidatura" })).toBeNull();
  });

  it("404 → E3 amb «Ves a Oportunitats»; una altra fallada → E1 amb reintent", async () => {
    respondWith(base({ "/tenders/t1": notFound() }));
    render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { name: "Aquesta licitació ja no hi és" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ves a Oportunitats" }).getAttribute("href")).toBe("/licitacions");
  });

  it("error de xarxa → E1 amb reintent", async () => {
    respondWith(base({ "/tenders/t1": new Error("503") }));
    render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar la licitació" })).toBeTruthy();
    const before = get.mock.calls.filter((call) => call[0] === "/tenders/t1").length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(get.mock.calls.filter((call) => call[0] === "/tenders/t1").length).toBeGreaterThan(before);
  });
});
