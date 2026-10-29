import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TenderDetail } from "@/lib/tenders";
import { awardScoring, reviewOverview } from "@/test/review-fixture";

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
const requestProcessing = vi.fn();
const track = vi.fn();
let jobRequest: import("@/lib/jobs").JobRequest | null = null;
/** L'`onFinished` de cada tipus de feina, per fer-la acabar des del test. */
const finishers: Record<string, (request: import("@/lib/jobs").JobRequest) => void | Promise<void>> = {};
vi.mock("@/lib/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/jobs")>();
  return {
    ...actual,
    useJobRequest: (
      _companyId: string | null,
      _tenderId: string,
      kind: string,
      onFinished: (request: import("@/lib/jobs").JobRequest) => void | Promise<void>,
    ) => {
      finishers[kind] = onFinished;
      return { request: jobRequest, active: jobRequest?.status === "accepted", track };
    },
    requestProcessing: (...args: unknown[]) => requestProcessing(...args),
  };
});
vi.mock("@/components/ComplianceSection", () => ({ ComplianceSection: () => <div data-testid="compliance">Compliment (secció actual)</div> }));
vi.mock("@/components/RequirementAnalysis", () => ({ RequirementAnalysisSection: () => <div data-testid="analysis">Anàlisi del plec (secció actual)</div> }));
vi.mock("@/components/TenderChangeHistory", () => ({ TenderChangeHistory: () => <div data-testid="history">Historial de canvis (secció actual)</div> }));

import { ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";

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
    { id: "d2", name: "Plec tècnic.pdf", document_type: "technical", url: "https://example.org/d2", latest_file: { file_id: "f2", version_number: 1, download_status: "failed", file_size: null, sha256: null, extraction_status: null, quality: null, page_count: null, text_char_count: null, ocr_candidate: false, extraction_id: null, processed_at: null, error_code: "timeout" } },
    { id: "d3", name: "Annex.pdf", document_type: "other", url: "https://example.org/d3", published_at: "2026-09-03", latest_file: { file_id: "f3", version_number: 2, download_status: "downloaded", file_size: 120000, sha256: "abc", extraction_status: "extracted", quality: "good", page_count: 12, text_char_count: 34567, ocr_candidate: false, extraction_id: "e3", processed_at: "2026-09-15T10:00:00Z", error_code: null } },
  ] as unknown as TenderDetail["documents"],
  removed_documents: [],
  package: null,
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
    "/companies/c1/tenders/t1/job-requests": [],
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
    requestProcessing.mockReset();
    track.mockReset();
    jobRequest = null;
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
    expect(header.textContent).toContain("2 bloquejos confirmats · 3 per revisar");
    expect(header.textContent).toContain("240.000");
    expect(header.textContent).toContain("Publicada 2 cops");
    expect(header.textContent).not.toMatch(/probabilitat/i);
    expect(screen.getByRole("button", { name: "Continua la candidatura" })).toBeTruthy();

    const tabs = screen.getAllByRole("tab").map((tab) => `${tab.textContent?.replace(/\s+/g, " ").trim()}${tab.getAttribute("aria-selected") === "true" ? " *" : ""}`);
    expect(tabs).toEqual(["Resum *", "Plecs3", "Requisits extrets14", "Anàlisi", "Historial"]);

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
    // F8: el text apunta a la secció que existeix ara, no a l'antic «Historial de canvis».
    const publications = screen.getByText("Publicacions de la font").closest("section")!;
    expect(publications.textContent).toContain("«Com ha evolucionat l'expedient»");
    expect(publications.textContent).not.toContain("«Historial de canvis»");
  });

  it("Plecs: taula amb document, text extret, lectura i acció; cap codi intern com a text; C-1 dit clarament", async () => {
    search = "tab=plecs";
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent?.trim());
    expect(headers).toEqual(["", "Document", "Text extret", "Lectura", "Acció"]);

    const rows = [...container.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(3);
    const pill = (row: Element) => [...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pill(rows[0])).toContain("Sense text extret[neutral]");
    expect(rows[0].textContent).toContain("Processa");
    expect(pill(rows[1])).toContain("Error[blocking]");
    expect(rows[1].textContent).toContain("La descàrrega ha esgotat el temps d'espera.");
    expect(rows[1].textContent).not.toContain("timeout");
    expect(rows[1].querySelector("[title='timeout']")).not.toBeNull();
    expect(rows[1].textContent).toContain("Reprocessa");
    expect(pill(rows[2])).toContain("Analitzat[ready]");
    expect(rows[2].textContent).toContain("12 pàg. · 34.567 caràcters");
    expect(rows[2].textContent).toContain("v2");
    expect(rows[2].querySelector("a[href='/licitacions/t1/documents/d3']")?.textContent).toContain("Obre el text");
    expect(rows[2].querySelector("a[href='https://example.org/d3']")).not.toBeNull();

    // C-1: la tria només processa; no promet limitar l'anàlisi.
    expect(container.textContent).toContain("La selecció només governa el processament; no limita quins documents utilitza l'anàlisi de requisits.");
    expect(container.textContent).not.toMatch(/Analitza els \d+ triats/);
  });

  it("Plecs: triar dos documents crea una sola feina de processat amb els dos ids", async () => {
    search = "tab=plecs";
    respondWith(base());
    requestProcessing.mockResolvedValue({ id: "j1", job_type: "process_tender_documents", tender_id: "t1", status: "accepted", outcome: null, reused_existing: false, result_run_id: null, error_code: null, requested_at: "2026-09-16T10:00:00Z", finished_at: null, progress: null });
    render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    fireEvent.click(screen.getByLabelText("Selecciona Plec administratiu.pdf"));
    fireEvent.click(screen.getByLabelText("Selecciona Annex.pdf"));
    expect(screen.getByText("2 triats")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Processa 2 documents" }));
    await flush();
    expect(requestProcessing).toHaveBeenCalledTimes(1);
    expect(requestProcessing).toHaveBeenCalledWith("c1", "t1", ["d1", "d3"]);
    expect(track).toHaveBeenCalledWith(expect.objectContaining({ id: "j1", status: "accepted" }));
  });

  it("Plecs: feina en curs → pastilla amb progrés i accions desactivades; un 200 no vol dir processat", async () => {
    search = "tab=plecs";
    jobRequest = { id: "j1", job_type: "process_tender_documents", tender_id: "t1", status: "accepted", outcome: null, reused_existing: false, result_run_id: null, error_code: null, requested_at: "2026-09-16T10:00:00Z", finished_at: null, progress: { done: 1, total: 3 } };
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    expect([...container.querySelectorAll("[data-level='running']")].map((p) => p.textContent?.trim())).toContain("Processant · 1 de 3");
    const buttons = screen.getAllByRole("button", { name: /Processa|Reprocessa/ });
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const button of buttons) expect((button as HTMLButtonElement).disabled).toBe(true);
    expect([...container.querySelectorAll("tbody [data-level]")].map((p) => p.textContent?.trim())).toContain("Sense text extret");
  });

  it("Plecs: feina acceptada però a la cua → «En cua · pendent d'inici», mai «Processant»; les files guarden el seu estat", async () => {
    search = "tab=plecs";
    jobRequest = { id: "j1", job_type: "process_tender_documents", tender_id: "t1", status: "accepted", outcome: null, reused_existing: false, result_run_id: null, error_code: null, requested_at: "2026-09-16T10:00:00Z", finished_at: null, progress: null, started: false };
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    const pills = [...container.querySelectorAll("[data-level]")].map((p) => p.textContent?.trim());
    expect(pills).toContain("En cua · pendent d'inici");
    expect(pills.some((label) => label?.startsWith("Processant"))).toBe(false);
    // L'estat de cada document és el que té desat, no el de la feina.
    expect([...container.querySelectorAll("tbody [data-level]")].map((p) => p.textContent?.trim())).toContain("Sense text extret");
    for (const button of screen.getAllByRole("button", { name: /Processa|Reprocessa/ })) expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("Plecs: feina fallada → ErrorState amb la frase del diccionari i el codi només al detall tècnic", async () => {
    search = "tab=plecs";
    jobRequest = { id: "j1", job_type: "process_tender_documents", tender_id: "t1", status: "failed", outcome: null, reused_existing: false, result_run_id: null, error_code: "worker_lost", requested_at: "2026-09-16T10:00:00Z", finished_at: "2026-09-16T10:01:00Z", progress: null };
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    expect(screen.getByRole("heading", { name: "L'execució es va interrompre. Torna-ho a demanar." })).toBeTruthy();
    const details = [...container.querySelectorAll("details")].find((d) => d.textContent?.includes("worker_lost"));
    expect(details).toBeTruthy();
    expect(screen.getByRole("heading", { name: /interrompre/ }).textContent).not.toContain("worker_lost");
  });

  it("Plecs: feina parcial → bàner d'atenció; sense permís → botons visibles, desactivats i amb la raó", async () => {
    search = "tab=plecs";
    jobRequest = { id: "j1", job_type: "process_tender_documents", tender_id: "t1", status: "succeeded", outcome: "partial", reused_existing: false, result_run_id: null, error_code: null, requested_at: "2026-09-16T10:00:00Z", finished_at: "2026-09-16T10:01:00Z", progress: { done: 3, total: 3 } };
    respondWith(base({ "/companies/c1/analysis-limits": { can_process_documents: false, can_confirm: false, active_now: 0, confirm_roles: ["owner", "admin"] } }));
    render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("Algun document no s'ha pogut processar");
    expect(screen.getByText(/Només .* pot demanar el processat dels plecs\./)).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: /Processa|Reprocessa/ });
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const button of buttons) expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("Plecs: una licitació de la PSCP porta «Actualitza documents de la PSCP»; una altra no", async () => {
    search = "tab=plecs";
    respondWith(base({ "/tenders/t1": { ...detail, portal_sync: { available: true, reason: null, retry_after_seconds: 0 } } }));
    const first = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    expect(first.container.querySelector("[data-portal-sync]")).not.toBeNull();
    expect((screen.getByRole("button", { name: "Actualitza documents de la PSCP" }) as HTMLButtonElement).disabled).toBe(false);
    first.unmount();

    respondWith(base());
    const other = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");
    expect(other.container.querySelector("[data-portal-sync]")).toBeNull();
    expect(screen.queryByRole("button", { name: /Actualitza documents de la PSCP/ })).toBeNull();
  });

  it("Plecs: en acabar la sincronització es rellegeixen la fitxa, l'anàlisi i el compliment, sense esquelet", async () => {
    search = "tab=plecs";
    const pscp = { ...detail, portal_sync: { available: true, reason: null, retry_after_seconds: 0 } };
    respondWith(base({ "/tenders/t1": pscp }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Plec tècnic.pdf");

    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith(base({ "/tenders/t1": pending }));
    const before = get.mock.calls.length;
    await act(async () => {
      void finishers.sync_portal_documents({ id: "j9", job_type: "sync_portal_documents", tender_id: "t1", status: "succeeded", outcome: "executed", reused_existing: false, result_run_id: null, error_code: null, requested_at: "2026-10-29T16:42:58Z", finished_at: "2026-10-29T16:43:09Z", progress: null });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    // Mentre es torna a llegir, la fitxa continua a la pantalla (cap esquelet, la barra hi és).
    expect(container.querySelectorAll(".animate-skeleton").length).toBe(0);
    expect(container.querySelector("[data-portal-sync]")).not.toBeNull();

    await act(async () => {
      pending.resolve({
        ...pscp,
        documents: [...pscp.documents, { id: "d4", name: "Preguntes i respostes (PSCP)", document_type: "other", url: "pscp-portal://qa", latest_file: null }],
        portal_sync: { available: true, reason: null, retry_after_seconds: 300 },
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByText("Preguntes i respostes (PSCP)")).toBeTruthy();
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 5 minuts.");
    expect((screen.getByRole("button", { name: "Actualitza documents de la PSCP" }) as HTMLButtonElement).disabled).toBe(true);
    const paths = get.mock.calls.slice(before).map((call) => call[0] as string);
    expect(paths).toContain("/tenders/t1/analysis/requirements?company_id=c1");
    expect(paths).toContain("/companies/c1/tenders/t1/compliance");
    expect(paths).toContain("/tenders/t1/history");
  });

  it("Plecs: amb 0 documents, una licitació de la PSCP continua oferint el botó", async () => {
    search = "tab=plecs";
    respondWith(base({ "/tenders/t1": { ...detail, documents: [], portal_sync: { available: true, reason: null, retry_after_seconds: 0 } } }));
    render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { name: "Sense documents publicats" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Actualitza documents de la PSCP" })).toBeTruthy();
  });

  it("Plecs: sense documents → buit amb sortida a la font", async () => {
    search = "tab=plecs";
    respondWith(base({ "/tenders/t1": { ...detail, documents: [] } }));
    render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { name: "Sense documents publicats" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Obre l'expedient a la font/ }).getAttribute("href")).toBe("https://example.org/exp-1");
  });

  it("Plecs: amb 0 documents vigents la línia de cobertura del paquet es pinta igualment (N4)", async () => {
    search = "tab=plecs";
    respondWith(base({ "/tenders/t1": { ...detail, documents: [], package: { status: "incomplete", gaps: [{ code: "declared_by_operator", key: null, would_provide: ["requirements"] }] } } }));
    const { container } = render(<TenderDetailPage />);
    expect(await screen.findByRole("heading", { name: "Sense documents publicats" })).toBeTruthy();
    expect(container.textContent).toContain("Cap document analitzable");
    expect(container.textContent).toContain("Paquet incomplet");
    expect(container.textContent).toContain("Falta un document (declarat per l'operador)");
    expect(container.textContent).not.toMatch(/declared_by_operator/);
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
    // Sense saber el perfil no s'afirma res: ni «per a totes» ni cap càlcul global.
    expect(screen.getByText("Encara no s'ha calculat")).toBeTruthy();
    expect(container.textContent).not.toMatch(/per a totes/);
    expect(screen.queryByRole("link", { name: /Recalcula el pre-fit/ })).toBeNull();
  });

  it("pre-fit sense perfil: diu quan es calcula i no ofereix calcular-lo", async () => {
    respondWith(base({
      "/companies/c1/matches/by-tender/t1": notFound(),
      "/companies/c1/summary": { counts: { cpv_preferences: 0, territories: 0, contract_preferences: 0 } },
    }));
    render(<TenderDetailPage />);
    expect(
      await screen.findByText("El pre-fit només es calcula quan el perfil té CPV, territoris o preferències de contracte"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Calcula'l ara" })).toBeNull();
  });

  it("pre-fit amb perfil: «Calcula'l ara» el calcula només per a aquesta licitació", async () => {
    respondWith(base({
      "/companies/c1/matches/by-tender/t1": notFound(),
      "/companies/c1/summary": { counts: { cpv_preferences: 3, territories: 0, contract_preferences: 0 } },
    }));
    post.mockResolvedValue({ tenders_evaluated: 1 });
    render(<TenderDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Calcula'l ara" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/matches/recalculate?tender_id=t1", {});
    expect(get.mock.calls.filter((call) => call[0] === "/companies/c1/matches/by-tender/t1").length).toBeGreaterThan(1);
  });

  it.each([
    ["només territoris", { cpv_preferences: 0, territories: 2, contract_preferences: 0 }],
    ["només preferències de contracte", { cpv_preferences: 0, territories: 0, contract_preferences: 1 }],
    ["CPV i territoris", { cpv_preferences: 4, territories: 1, contract_preferences: 0 }],
  ])("C5: perfil amb dades (%s) i sense càlcul → «Encara no s'ha calculat» + «Calcula'l ara», mai la condició del perfil", async (_name, counts) => {
    respondWith(base({
      "/companies/c1/matches/by-tender/t1": notFound(),
      "/companies/c1/summary": { counts },
    }));
    const { container } = render(<TenderDetailPage />);
    expect(await screen.findByRole("button", { name: "Calcula'l ara" })).toBeTruthy();
    expect(screen.getByText("Encara no s'ha calculat")).toBeTruthy();
    expect(container.textContent).not.toContain("El pre-fit només es calcula quan el perfil té");
  });

  it("C5: només amb el perfil realment buit diu la condició del perfil, i sense botó de càlcul", async () => {
    respondWith(base({
      "/companies/c1/matches/by-tender/t1": notFound(),
      "/companies/c1/summary": { counts: { cpv_preferences: 0, territories: 0, contract_preferences: 0 } },
    }));
    const { container } = render(<TenderDetailPage />);
    expect(await screen.findByText("El pre-fit només es calcula quan el perfil té CPV, territoris o preferències de contracte")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Calcula'l ara" })).toBeNull();
    expect(container.textContent).not.toContain("Encara no s'ha calculat");
  });

  it("Objecte: sense descripció publicada diu «Objecte no publicat al feed» i no fa servir el títol", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, description: null } }));
    render(<TenderDetailPage />);
    const object = await screen.findByText("Objecte no publicat al feed");
    const fact = object.closest("div")!;
    expect(fact.textContent).not.toContain(detail.title);
  });

  it("Plecs: badges d'estat, àlies plegats, retirats amb data, «N de M» i avís de paquet abans de la quota", async () => {
    search = "tab=plecs";
    const base_ = { source_document_type_code: null, mime_type: "application/pdf", published_at: null, removed_at: null, contribution_status: "not_applicable", duplicate_of_document_id: null };
    const extracted = { file_id: "f1", version_number: 1, download_status: "downloaded", origin: "fetched", file_size: 10, sha256: "a", mime_type: "application/pdf", text_extraction_supported: true, analysis_status: "extracted", extraction_status: "extracted", quality: "good", page_count: 3, text_char_count: 300, ocr_candidate: false, extraction_id: "e1", processed_at: null, error_code: null };
    respondWith(base({
      "/tenders/t1": {
        ...detail,
        documents: [
          { ...base_, id: "p1", name: "PCAP.pdf", document_type: "administrative_terms", url: "https://example.org/p1", origin: "feed", analyzable: true, latest_file: extracted },
          { ...base_, id: "p2", name: "PCAP (còpia).pdf", document_type: "administrative_terms", url: "https://example.org/p2", origin: "feed", analyzable: false, duplicate_of_document_id: "p1", latest_file: extracted },
          { ...base_, id: "p3", name: "Annex 3.pdf", document_type: "additional", url: "https://contractaciopublica.cat/a3", origin: "operator_url", contribution_status: "pending_verification", analyzable: false, latest_file: null },
          { ...base_, id: "p4", name: "PPT.pdf", document_type: "technical_terms", url: "https://example.org/p4", origin: "feed", analyzable: true, latest_file: null },
        ],
        removed_documents: [
          { ...base_, id: "r1", name: "PPT antic.pdf", document_type: "technical_terms", url: "https://example.org/r1", origin: "feed", analyzable: false, removed_at: "2026-09-30T10:00:00Z", latest_file: null },
        ],
        package: { status: "incomplete", gaps: [{ code: "referenced_document_missing", key: "DOC-03", would_provide: [] }] },
      },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByText("Annex 3.pdf");
    expect(screen.getByText("1 de 2 documents analitzables llegits")).toBeTruthy();
    expect(screen.getByText("pendent de verificació")).toBeTruthy();
    expect(screen.getByText("àlies de PCAP.pdf")).toBeTruthy();
    // L'àlies no és una fila pròpia: va plegat sota el canònic.
    expect(container.querySelectorAll("tbody tr")).toHaveLength(3);
    expect(screen.getByText("PPT antic.pdf")).toBeTruthy();
    expect(screen.getByText(/Retirat de la font \(des del /)).toBeTruthy();
    expect(container.textContent).toContain("Falta un document que cita el plec (DOC-03)");
    expect(container.textContent).toContain("impacte desconegut");
    expect(container.textContent).not.toMatch(/operator_url|pending_verification|referenced_document_missing/);
  });

  it("fitxa sense cap anàlisi del plec: el capçal ho diu amb accés a analitzar-lo, mai «0 de 0»", async () => {
    respondWith(base({ "/tenders/t1/analysis/requirements": { run: null, requirements: [], award_criteria: [], is_stale: false } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Encara no hi ha cap anàlisi del plec");
    expect(within(header as HTMLElement).getByRole("link", { name: "Analitza el plec" }).getAttribute("href")).toBe("/licitacions/t1?tab=requisits");
    expect(header.textContent).not.toMatch(/\b0 de 0\b/);
  });

  it("licitació manual amb requisits escrits a mà i sense run: `analysis_state` mana (current), cap «sense anàlisi» ni CTA (M1)", async () => {
    respondWith(base({
      "/tenders/t1": { ...detail, traceability: { source_code: "manual", source_name: "Manual", external_id: "x", source_url: null, latest_source_record_id: null, fetched_at: null } },
      "/tenders/t1/analysis/requirements": {
        run: null,
        requirements: [{ id: "m1", origin: "manual", title: "Assegurança RC", category: "insurance", obligation: "mandatory", archived_at: null, evidence: [], citations: [] }],
        award_criteria: [],
        is_stale: false,
        analysis_state: "current",
      },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Requisits escrits a mà · 1");
    expect(header.textContent).not.toContain("Encara no hi ha cap anàlisi del plec");
    expect(within(header as HTMLElement).queryByRole("link", { name: "Analitza el plec" })).toBeNull();
  });

  it("a la pestanya Requisits la capçalera no duplica la CTA «Analitza el plec» (A8)", async () => {
    search = "tab=requisits";
    respondWith(base({ "/tenders/t1/analysis/requirements": { run: null, requirements: [], award_criteria: [], is_stale: false, analysis_state: "absent" } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Encara no hi ha cap anàlisi del plec");
    expect(within(header as HTMLElement).queryByRole("link", { name: "Analitza el plec" })).toBeNull();
  });

  it("Objecte d'una licitació manual sense descripció: «Sense descripció», mai «no publicat al feed» (M5)", async () => {
    respondWith(base({
      "/tenders/t1": { ...detail, description: null, traceability: { source_code: "manual", source_name: "Manual", external_id: "x", source_url: null, latest_source_record_id: null, fetched_at: null } },
    }));
    render(<TenderDetailPage />);
    expect(await screen.findByText("Sense descripció")).toBeTruthy();
    expect(screen.queryByText("Objecte no publicat al feed")).toBeNull();
  });

  it("la candidatura en estat d'anàlisi s'etiqueta segons `analysis_state` (A13)", async () => {
    respondWith(base({
      "/companies/c1/tenders/t1/workspace": { ...workspace, status: "analysis", human_decision: null, dossier_coverage: { analysis_state: "absent", mandatory_total: null, covered: 0, missing_document: 0, needs_review: 0, not_applicable: 0, expired_documents: 0, optional_total: 0, optional_covered: 0 } },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    expect(container.querySelector("[data-tender-header]")!.textContent).toContain("Pendent d'anàlisi");
  });

  it("Plecs: l'estat de cada document segueix la lectura i l'anàlisi (A5), i el recompte no compta els àlies", async () => {
    search = "tab=plecs";
    const base_ = { source_document_type_code: null, mime_type: "application/pdf", published_at: null, removed_at: null, contribution_status: "not_applicable", duplicate_of_document_id: null, origin: "feed", analyzable: true };
    const extracted = { file_id: "f1", version_number: 1, download_status: "downloaded", origin: "fetched", file_size: 10, sha256: "a", mime_type: "application/pdf", text_extraction_supported: true, analysis_status: "extracted", extraction_status: "extracted", quality: "good", page_count: 3, text_char_count: 300, ocr_candidate: false, extraction_id: "e1", processed_at: null, error_code: null };
    const documents = [
      { ...base_, id: "p1", name: "PCAP.pdf", document_type: "administrative_terms", url: "https://example.org/p1", latest_file: extracted },
      { ...base_, id: "p2", name: "PPT.pdf", document_type: "technical_terms", url: "https://example.org/p2", latest_file: null },
      { ...base_, id: "p3", name: "PCAP (còpia).pdf", document_type: "administrative_terms", url: "https://example.org/p3", analyzable: false, duplicate_of_document_id: "p1", latest_file: extracted },
    ];
    respondWith(base({
      "/tenders/t1": { ...detail, documents, package: { status: "complete", gaps: [], section_status: {} } },
      "/tenders/t1/analysis/requirements": { run: null, requirements: [], award_criteria: [], is_stale: false, analysis_state: "absent" },
    }));
    const { container, unmount } = render(<TenderDetailPage />);
    await screen.findByText("PPT.pdf");
    expect(container.textContent).toContain("Plecs (2)");
    expect(container.textContent).toContain("Text extret · requisits encara no analitzats");
    expect(container.textContent).toContain("Sense text extret");
    expect(container.textContent).toContain("Paquet complet");
    expect(container.textContent).not.toContain("Analitzat");
    unmount();

    respondWith(base({
      "/tenders/t1": { ...detail, documents, package: { status: "complete", gaps: [], section_status: {} } },
      "/tenders/t1/analysis/requirements": { ...analysis, analysis_state: "current" },
    }));
    const second = render(<TenderDetailPage />);
    await screen.findByText("PPT.pdf");
    const rows = [...second.container.querySelectorAll("tbody tr")];
    expect(rows.find((row) => row.textContent?.includes("PCAP.pdf"))?.textContent).toContain("Analitzat");
    expect(rows.find((row) => row.textContent?.includes("PPT.pdf"))?.textContent).toContain("Sense text extret");
  });

  it("els requisits es llegeixen des de l'empresa activa (`company_id`)", async () => {
    respondWith(base());
    render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(get.mock.calls.map((call) => call[0])).toContain("/tenders/t1/analysis/requirements?company_id=c1");
  });

  it("estat i tipus que el diccionari no coneix: fallbacks catalans, mai el codi cru", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, status: "nou_estat", procedure_type: "nou_proc", contract_type: "toString" } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(container.textContent).toContain("Estat desconegut");
    expect(container.textContent).toContain("Tipus desconegut");
    expect(container.textContent).not.toMatch(/nou_estat|nou_proc|toString/);
  });

  it("termini passat: «Fora de termini» al capçal i l'estat cru de la font a part (A12)", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, status: "open", deadline_at: "2026-09-01T10:00:00Z" } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Fora de termini");
    // F2: l'estat cru es diu «Publicada»; «En termini» no apareix enlloc de la fitxa.
    expect(header.textContent).toContain("Estat a la font: Publicada");
    expect(container.textContent).not.toContain("En termini");
    const pills = [...header.querySelectorAll("[data-level]")].map((pill) => pill.textContent?.trim());
    expect(pills).not.toContain("En termini");
  });

  it("termini obert: l'estat de la font tal qual i cap «Estat a la font»", async () => {
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("En termini");
    expect(header.textContent).not.toContain("Fora de termini");
    expect(header.textContent).not.toContain("Estat a la font");
  });

  it("ja no disponible a la font: etiqueta amb la data al capçal, sense canviar l'estat ni dir «retirada»", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, source_removed_at: "2026-09-20T08:00:00Z" } }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    const header = container.querySelector("[data-tender-header]")!;
    expect(header.textContent).toContain("Ja no disponible a la font (des del ");
    expect(header.textContent).toMatch(/\(des del [^)]+2026\)/);
    expect(header.textContent).toContain("En termini");
    expect(header.textContent).not.toMatch(/retirada|anul·lada/i);
  });

  it("sense làpida, el capçal no diu res de la disponibilitat a la font", async () => {
    respondWith(base());
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    expect(container.querySelector("[data-tender-header]")!.textContent).not.toContain("Ja no disponible");
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

  it("Historial: la traçabilitat tècnica (hashes, SourceRecord) va plegada sota «Detalls tècnics» (D5)", async () => {
    search = "tab=historial";
    respondWith(base({
      "/tenders/t1": {
        ...detail,
        traceability: { source_code: "placsp_agregadas", source_name: "PLACSP", external_id: "ext-1", source_url: null, latest_source_record_id: "sr-99", fetched_at: "2026-09-10T00:00:00Z", parser_version: "agregadas-v3", raw_hash: "deadbeef".repeat(8) },
      },
    }));
    render(<TenderDetailPage />);
    await screen.findByTestId("history");
    const summary = screen.getByText("Detalls tècnics");
    const details = summary.closest("details")!;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("SourceRecord actual");
    expect(details.textContent).toContain("sr-99");
    expect(details.textContent).toContain("deadbeef");
  });

  it("CPV: el nom oficial al costat del codi, i mai el codi repetit com a descripció (D4)", async () => {
    respondWith(base({
      "/tenders/t1": {
        ...detail,
        cpvs: [
          { cpv_code: "32412100", description: "Xarxa de telecomunicacions", is_main: true },
          { cpv_code: "32500000", description: "CPV 32500000", is_main: false },
        ],
      },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    expect(container.textContent).toContain("Xarxa de telecomunicacions");
    expect(container.textContent).toContain("32500000");
    expect(container.textContent).not.toContain("CPV 32500000");
  });

  it("auditoria UI P1-05: el primer CPV del feed no es diu «principal», i es conserven tots els CPV", async () => {
    // El cas de L'Escala: la font publica 32562100 primer; el principal del PSCP i del DOC-03 és 64200000.
    respondWith(base({
      "/tenders/t1": {
        ...detail,
        cpvs: [
          { cpv_code: "32562100", description: "Cables de fibra òptica", is_main: true },
          { cpv_code: "64200000", description: "Serveis de telecomunicacions", is_main: false },
          { cpv_code: "45314300", description: "Instal·lació d'infraestructures per a cables", is_main: false },
        ],
      },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    const card = screen.getByText("Classificació del contracte segons el vocabulari comú europeu.").closest("section")!;
    // Cap etiqueta de CPV diu «principal»: el marcat diu el que és, el primer a la font.
    const chips = [...card.querySelectorAll("span.font-mono")].map((el) => el.parentElement!);
    const kickers = chips.map((chip) => chip.lastElementChild?.textContent ?? "");
    expect(kickers).not.toContain("principal");
    expect(chips[0].textContent).toContain("32562100");
    expect(chips[0].textContent).toContain("primer a la font");
    // Tots els CPV originals hi són; el primer de la font al davant.
    const codes = chips.map((chip) => chip.querySelector("span.font-mono")!.textContent);
    expect(codes[0]).toBe("32562100");
    expect([...codes].sort()).toEqual(["32562100", "45314300", "64200000"]);
    // I es diu per què: la font no indica quin és el principal.
    expect(container.querySelector("[data-cpv-provenance]")?.textContent).toContain("no indica quin CPV és el principal");
  });

  it("auditoria UI P1-05: sense cap CPV marcat no s'afegeix la nota de procedència", async () => {
    respondWith(base({
      "/tenders/t1": { ...detail, cpvs: [{ cpv_code: "64200000", description: "Serveis de telecomunicacions", is_main: false }] },
    }));
    const { container } = render(<TenderDetailPage />);
    await screen.findByRole("heading", { level: 1 });
    await flush();
    expect(container.textContent).toContain("64200000");
    expect(container.querySelector("[data-cpv-provenance]")).toBeNull();
    expect(container.textContent).not.toContain("primer a la font");
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

  it("Resum: la revisió de l'empresa es descobreix d'entrada, amb xifres vives i una sola crida principal", async () => {
    respondWith(base({
      "/companies/c1/tenders/t1/review/overview": reviewOverview,
      "/companies/c1/tenders/t1/review/award-criteria?view=scoring": awardScoring,
    }));
    render(<TenderDetailPage />);
    const metrics = await screen.findByLabelText("Resum de la revisió");
    await flush();

    const value = (label: string) => [...metrics.querySelectorAll("dt")].find((dt) => dt.textContent === label)?.nextElementSibling?.textContent;
    expect(value("Requisits")).toBe(String(reviewOverview.total_requirements));
    expect(value("Cal revisar")).toBe(String(reviewOverview.needs_review_count));
    expect(value("Criteris que puntuen")).toBe("9");
    expect(value("Punts")).toBe("100 punts");
    const ctas = screen.getAllByRole("link", { name: "Obre la revisió" });
    expect(ctas).toHaveLength(1);
    expect(ctas[0].getAttribute("href")).toBe("/licitacions/t1/revisio");

    // A «Requisits extrets» ja no es repeteix la targeta gran.
    fireEvent.click(screen.getByRole("tab", { name: /Requisits extrets/ }));
    expect(await screen.findByTestId("analysis")).toBeTruthy();
    expect(screen.queryByLabelText("Resum de la revisió")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Revisió de la licitació" })).toBeNull();
  });
  it("P2-06: els fets d'import diuen la seva base i, si la font no la publica, ho diuen", async () => {
    respondWith(base({ "/tenders/t1": { ...detail, estimated_value: "245079.30", base_budget: null, base_budget_without_tax: "204232.75" } }));
    render(<TenderDetailPage />);
    await flush();
    const facts = document.body.textContent ?? "";
    // Cada etiqueta, amb el seu valor exacte (amb cèntims).
    // Intl posa un espai no separable davant de «€».
    const fact = (label: string) => screen.getByText(label, { selector: "p, dt" }).parentElement?.textContent?.replace(/\u00a0/g, " ");
    expect(fact("Valor estimat del contracte (VEC)")).toBe("Valor estimat del contracte (VEC)245.079,30 €");
    expect(fact("Pressupost base de licitació sense IVA (PBL)")).toBe("Pressupost base de licitació sense IVA (PBL)204.232,75 €");
    expect(fact("Pressupost base de licitació amb IVA")).toBe("Pressupost base de licitació amb IVANo consta a les dades de la font");
    expect(facts).not.toContain("Base sense impostos");
    expect(facts).toContain("valor estimat del contracte (VEC)");
    expect(document.querySelector("[data-amount-budget]")?.textContent).toContain("pressupost base de licitació sense IVA");
  });
  it("zeros (19-10-2026): el Resum diu que la PLACSP l'ha publicat a 0,00 € i la capçalera no el fa servir", async () => {
    const ZERO = "Publicat a 0,00 € a la PLACSP (no s'utilitza com a import econòmic)";
    respondWith(base({ "/tenders/t1": { ...detail, estimated_value: "0.00", base_budget: null, base_budget_without_tax: "30000.00" } }));
    render(<TenderDetailPage />);
    await flush();
    const fact = (label: string) => screen.getByText(label, { selector: "p, dt" }).parentElement?.textContent?.replace(/\u00a0/g, " ");
    expect(fact("Valor estimat del contracte (VEC)")).toBe(`Valor estimat del contracte (VEC)${ZERO}`);
    expect(fact("Pressupost base de licitació sense IVA (PBL)")).toBe("Pressupost base de licitació sense IVA (PBL)30.000,00 €");
    const header = document.querySelector("[data-tender-header]")?.textContent?.replace(/\u00a0/g, " ") ?? "";
    expect(header).toContain("30.000,00 €");
    expect(header).toContain("pressupost base de licitació sense IVA");
    expect(header).not.toMatch(/(^|[^\d.])0,00 €/);
  });
  it("zeros (19-10-2026): amb VEC i PBL sense IVA a 0,00 €, tots dos fets diuen el text del zero i la capçalera, que no consta", async () => {
    const ZERO = "Publicat a 0,00 € a la PLACSP (no s'utilitza com a import econòmic)";
    respondWith(base({ "/tenders/t1": { ...detail, estimated_value: "0.00", base_budget: null, base_budget_without_tax: "0.00" } }));
    render(<TenderDetailPage />);
    await flush();
    const fact = (label: string) => screen.getByText(label, { selector: "p, dt" }).parentElement?.textContent?.replace(/\u00a0/g, " ");
    expect(fact("Valor estimat del contracte (VEC)")).toBe(`Valor estimat del contracte (VEC)${ZERO}`);
    expect(fact("Pressupost base de licitació sense IVA (PBL)")).toBe(`Pressupost base de licitació sense IVA (PBL)${ZERO}`);
    expect(fact("Pressupost base de licitació amb IVA")).toBe("Pressupost base de licitació amb IVANo consta a les dades de la font");
    expect(document.querySelectorAll("[data-amount-zero]").length).toBe(2);
    expect(document.querySelector("[data-tender-header]")?.textContent).toContain("No consta a les dades de la font");
    expect(document.querySelector("[data-amount-budget]")).toBeNull();
  });
  it("P2-06 / F-06e: un lot amb VEC i PBL diferents mostra cada xifra a la seva columna", async () => {
    const lot = {
      id: "l1", external_lot_id: "1", lot_number: 1, title: "Lot de connectivitat", description: null,
      estimated_value: "23456.50", base_budget: null, base_budget_without_tax: "19876.25", currency: "EUR",
      place_of_execution: null, nuts_code: null, deadline_at: null, cpvs: [],
    };
    respondWith(base({ "/tenders/t1": { ...detail, lot_count: 1, lots: [lot] } }));
    render(<TenderDetailPage />);
    await flush();
    const row = screen.getByText("Lot de connectivitat").closest("tr");
    const text = (node: Element | null | undefined) => node?.textContent?.replace(/\u00a0/g, " ");
    // Cada xifra a la seva columna (la capçalera en diu la base): VEC, PBL sense IVA, PBL amb IVA.
    const headers = Array.from(row!.closest("table")!.querySelectorAll("th")).map((th) => th.textContent);
    expect(headers).toEqual(expect.arrayContaining(["VEC", "PBL sense IVA", "PBL amb IVA"]));
    const cells = Array.from(row!.querySelectorAll("[data-lot-amount]")).map((cell) => [cell.getAttribute("data-lot-amount"), text(cell)]);
    expect(cells).toEqual([
      ["placsp", "23.456,50 €"],
      ["placsp", "19.876,25 €"],
      ["none", "—No consta a les dades de la font"],
    ]);
    // Tot de la PLACSP: cap indicació de la PSCP ni atribució.
    expect(document.querySelector("[data-lots-pscp-note]")).toBeNull();
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
  });
});

describe("Fitxa de licitació: PBL amb IVA de la PSCP (F-06a)", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    replace.mockReset();
    jobRequest = null;
    search = "";
  });

  const LINK = "https://contractaciopublica.cat/ca/detall-publicacio/x/300001";
  const UPDATED = "2026-10-16T06:30:00Z";
  const NOT_PUBLISHED = "No consta a les dades de la font";
  // El text oficial ve de l'API (una sola font de veritat): la web el pinta tal qual.
  const OFFICIAL =
    "Font: Generalitat de Catalunya. Departament d'Economia i Finances — Contractació pública a Catalunya: publicacions a la Plataforma de serveis de contractació pública (dades obertes). Llicència oberta d'ús d'informació – Catalunya.";
  const ATTRIBUTION_START = `${OFFICIAL} Darrera actualització: `;
  const observation = (overrides: Record<string, unknown> = {}) => ({
    status: "accepted",
    amount: "247121.63",
    currency: "EUR",
    published_zero: false,
    failed_checks: [],
    publication_url: LINK,
    source_updated_at: UPDATED,
    retrieved_at: "2026-10-16T12:00:00Z",
    source_code: "pscp_open_data:ybgg-dgi6",
    ...overrides,
  });
  const lotOf = (id: string, title: string) => ({
    id, external_lot_id: id, lot_number: Number(id.slice(1)), title, description: null,
    estimated_value: null, base_budget: null, base_budget_without_tax: null, currency: "EUR",
    place_of_execution: null, nuts_code: null, deadline_at: null, cpvs: [],
  });
  const text = (node: Element | null | undefined) => node?.textContent?.replace(/ /g, " ") ?? "";
  const field = (expedient: unknown, lots: Record<string, unknown> = {}) => ({ expedient, lots, attribution: OFFICIAL, source_updated_at: UPDATED });
  const amountFact = () => screen.getByText("Pressupost base de licitació amb IVA", { selector: "p" }).parentElement!;

  async function renderWith(observations: unknown, extra: Record<string, unknown> = {}) {
    respondWith(base({ "/tenders/t1": { ...detail, base_budget: null, budget_with_tax_observations: observations, ...extra } }));
    render(<TenderDetailPage />);
    await flush();
    return amountFact();
  }

  it("accepted: l'import amb cèntims, la font amb la data i l'enllaç a la publicació; atribució visible", async () => {
    const fact = await renderWith(field(observation()));
    expect(text(fact)).toContain("247.121,63 €");
    expect(text(fact)).not.toContain(NOT_PUBLISHED);
    expect(text(fact.querySelector("[data-pscp-source]"))).toBe(
      `Segons la PSCP (dades obertes de la Generalitat de Catalunya), actualitzat el ${formatDate(UPDATED)} · Veure la publicació ↗`,
    );
    const link = screen.getByRole("link", { name: "Veure la publicació ↗" });
    expect(link.getAttribute("href")).toBe(LINK);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(text(document.querySelector("[data-pscp-attribution]"))).toBe(`${ATTRIBUTION_START}${formatDate(UPDATED)}.`);
    // La capçalera no canvia: continua amb el VEC.
    expect(text(document.querySelector("[data-tender-header]"))).not.toContain("247.121,63");
  });

  it("accepted sense enllaç: la línia de font, sense «Veure la publicació»", async () => {
    const fact = await renderWith(field(observation({ publication_url: null })));
    expect(text(fact)).toContain("247.121,63 €");
    expect(text(fact.querySelector("[data-pscp-source]"))).toBe(
      `Segons la PSCP (dades obertes de la Generalitat de Catalunya), actualitzat el ${formatDate(UPDATED)}`,
    );
    expect(screen.queryByRole("link", { name: "Veure la publicació ↗" })).toBeNull();
  });

  it("no_value publicat a 0: el text exacte i la mateixa línia de font", async () => {
    const fact = await renderWith(field(observation({ status: "no_value", amount: "0.00", published_zero: true })));
    expect(text(fact)).toContain("Publicat a 0,00 € a la PSCP (sense pressupost base de despesa; no s'utilitza com a import econòmic)");
    expect(text(fact)).not.toContain(NOT_PUBLISHED);
    expect(text(fact.querySelector("[data-pscp-source]"))).toContain("Segons la PSCP (dades obertes de la Generalitat de Catalunya), actualitzat el");
    expect(screen.getByRole("link", { name: "Veure la publicació ↗" })).toBeTruthy();
    expect(document.querySelector("[data-pscp-attribution]")).not.toBeNull();
  });

  it("no_value nul: com abans, «No consta a les dades de la font», sense font ni atribució", async () => {
    const fact = await renderWith(field(observation({ status: "no_value", amount: null, published_zero: false })));
    expect(text(fact)).toBe(`Pressupost base de licitació amb IVA${NOT_PUBLISHED}`);
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
    expect(document.body.textContent).not.toContain("Segons la PSCP");
    expect(document.querySelector("[data-pscp-status]")).toBeNull();
  });

  it("sense observació: com abans, i sense atribució", async () => {
    const fact = await renderWith(field(null));
    expect(text(fact)).toBe(`Pressupost base de licitació amb IVA${NOT_PUBLISHED}`);
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
    expect(document.body.textContent).not.toContain("Segons la PSCP");
    expect(document.querySelector("[data-pscp-status]")).toBeNull();
  });

  it("conflict amb comprovacions: «Pendent de verificació…» com a valor, l'import només dins de l'avís, mai «No consta…»", async () => {
    const fact = await renderWith(
      field(observation({ status: "conflict", amount: "98600.00", failed_checks: ["organ", "pbl_without_tax", "vec", "lots"] })),
    );
    expect(fact.querySelector("[data-pscp-status]")?.firstElementChild?.textContent).toBe("Pendent de verificació (discrepància entre fonts)");
    expect(text(fact)).not.toContain(NOT_PUBLISHED);
    const notice = fact.querySelector("[data-pscp-notice]");
    expect(text(notice)).toBe(
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP (òrgan, PBL sense IVA, VEC i lots). No es fa servir fins que es verifiqui. · Veure la publicació ↗",
    );
    // L'import de la PSCP només surt dins de l'avís.
    expect(text(fact).replace(text(notice), "")).not.toContain("98.600,00");
    expect(document.querySelector("[data-pscp-source]")).toBeNull();
    expect(document.querySelector("[data-pscp-attribution]")).not.toBeNull();
  });

  it("conflict sense import: la variant «no hi publica cap import vàlid»", async () => {
    const fact = await renderWith(field(observation({ status: "conflict", amount: null, failed_checks: [], publication_url: null })));
    expect(text(fact)).toContain("Pendent de verificació (discrepància entre fonts)");
    expect(text(fact.querySelector("[data-pscp-notice]"))).toBe(
      "La PSCP no hi publica cap import vàlid com a pressupost base amb IVA. No es fa servir fins que es verifiqui.",
    );
    expect(text(fact)).not.toContain(NOT_PUBLISHED);
  });

  it("conflict sense cap comprovació contra la PLACSP: diu que no ha superat les de coherència de la font", async () => {
    const fact = await renderWith(field(observation({ status: "conflict", amount: "98600.00", failed_checks: [] })));
    expect(text(fact.querySelector("[data-pscp-notice]"))).toBe(
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no ha superat les comprovacions de coherència de la font. No es fa servir fins que es verifiqui. · Veure la publicació ↗",
    );
    expect(text(fact)).not.toContain("PLACSP");
  });

  it("conflict per lot_sum o per evaluation_error: l'avís diu el que ha passat, sense culpar la PLACSP", async () => {
    const fact = await renderWith(field(observation({ status: "conflict", amount: "98600.00", failed_checks: ["lot_sum"], publication_url: null })));
    expect(text(fact.querySelector("[data-pscp-notice]"))).toBe(
      "La suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP. No es fa servir fins que es verifiqui.",
    );
    expect(text(fact)).toContain("Pendent de verificació (discrepància entre fonts)");
    expect(text(fact)).not.toContain("PLACSP");
  });

  it("conflict per evaluation_error: «No s'ha pogut verificar…», mai «no hi publica cap import vàlid»", async () => {
    const fact = await renderWith(field(observation({ status: "conflict", amount: null, evaluation_error: true, publication_url: null })));
    expect(text(fact.querySelector("[data-pscp-notice]"))).toBe(
      "No s'ha pogut verificar l'import publicat a la PSCP. No es fa servir fins que es verifiqui.",
    );
    expect(text(fact)).not.toContain("no hi publica");
  });

  it("si la PLACSP ja publica el PBL amb IVA, el fet continua sent el seu", async () => {
    const fact = await renderWith(field(observation()), { base_budget: "200000" });
    expect(text(fact)).toBe("Pressupost base de licitació amb IVA200.000,00 €");
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
  });

  it("lots: accepted, conflict i no_value a 0, cadascun a la seva cel·la; mai un conflicte com a import", async () => {
    const lots = [lotOf("l1", "Lot u"), lotOf("l2", "Lot dos"), lotOf("l3", "Lot tres"), lotOf("l4", "Lot quatre")];
    await renderWith(
      field(null, {
        l1: observation({ amount: "32670.00" }),
        l2: observation({ status: "conflict", amount: "1000.00", failed_checks: ["organ"] }),
        l3: observation({ status: "no_value", amount: "0.00", published_zero: true }),
      }),
      { lots, lot_count: 4 },
    );
    const row = (title: string) => screen.getByText(title).closest("tr");
    expect(text(row("Lot u")?.querySelector("[data-pscp-lot]"))).toBe("32.670,00 € · PSCP ↗");
    expect(row("Lot u")?.querySelector("[data-pscp-lot-link]")?.getAttribute("href")).toBe(LINK);
    const conflict = row("Lot dos")?.querySelector("[data-pscp-lot]");
    expect(conflict?.firstElementChild?.textContent).toBe("Pendent de verificació (discrepància entre fonts)");
    expect(text(conflict?.querySelector("[data-pscp-notice]"))).toBe("No coincideix amb les dades de la PLACSP (òrgan). · Veure la publicació ↗");
    expect(text(row("Lot dos"))).not.toContain("1.000,00");
    expect(text(row("Lot tres")?.querySelector("[data-pscp-lot]"))).toBe("Publicat a 0,00 € a la PSCP");
    expect(row("Lot quatre")?.querySelector("[data-pscp-lot]")).toBeNull();
    // Cap «Sense import a la PLACSP» a cada cel·la: una sola indicació per a la taula.
    expect(document.body.textContent).not.toContain("Sense import a la PLACSP");
    expect(document.querySelectorAll("[data-lots-pscp-note]").length).toBe(1);
    expect(text(document.querySelector("[data-lots-pscp-note]"))).toBe(
      "Cap d'aquests lots no té imports a la PLACSP: els que es mostren són de la PSCP.",
    );
    // Només un lot amb observació ja porta l'atribució (una sola); el fet de l'expedient no canvia.
    expect(document.querySelectorAll("[data-pscp-attribution]").length).toBe(1);
    expect(text(document.querySelector("[data-pscp-attribution]"))).toBe(`${ATTRIBUTION_START}${formatDate(UPDATED)}.`);
    expect(text(amountFact())).toBe(`Pressupost base de licitació amb IVA${NOT_PUBLISHED}`);
  });

  it("F-06e: VEC i PBL sense IVA de la PSCP per lot; la PLACSP mana a cada cel·la", async () => {
    const lots = [
      { ...lotOf("l1", "Lot u"), estimated_value: "50000.00" },
      lotOf("l2", "Lot dos"),
    ];
    await renderWith(
      {
        ...field(null, { l2: observation({ amount: "43789.90" }) }),
        lots_estimated_value: {
          l1: observation({ amount: "72380.00", amount_kind: "estimated_value" }),
          l2: observation({ amount: "36190.00", amount_kind: "estimated_value" }),
        },
        lots_pbl_without_tax: {
          l1: observation({ amount: "30000.00", amount_kind: "pbl_without_tax" }),
          l2: observation({ status: "conflict", amount: "36190.00", amount_kind: "pbl_without_tax", failed_checks: ["lot_sum"] }),
        },
      },
      { lots, lot_count: 2 },
    );
    const cells = (title: string) =>
      Array.from(screen.getByText(title).closest("tr")!.querySelectorAll("[data-lot-amount]")).map((cell) => [
        cell.getAttribute("data-lot-amount"),
        text(cell),
      ]);
    // Lot u: el VEC de la PLACSP (50.000) guanya al de la PSCP; el PBL net, de la PSCP.
    expect(cells("Lot u")).toEqual([
      ["placsp", "50.000,00 €"],
      ["pscp", "30.000,00 € · PSCP ↗"],
      ["none", `—${NOT_PUBLISHED}`],
    ]);
    // Lot dos: VEC i PBL amb IVA de la PSCP; el PBL net en conflicte per la suma dels lots (sense culpar la PLACSP).
    const second = cells("Lot dos");
    expect(second[0]).toEqual(["pscp", "36.190,00 € · PSCP ↗"]);
    // Conflicte només de suma: la cel·la diu només «Pendent…»; el motiu va a la indicació de la taula.
    expect(second[1]).toEqual(["pscp", "Pendent de verificació (discrepància entre fonts)"]);
    expect(text(document.querySelector("[data-lots-sum-conflict]"))).toBe(
      "PBL sense IVA: la suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP. No es fa servir fins que es verifiqui. · Veure la publicació ↗",
    );
    expect(second[2]).toEqual(["pscp", "43.789,90 € · PSCP ↗"]);
    expect(text(screen.getByText("Lot dos").closest("tr"))).not.toContain("36.190,00 € · PSCP ↗36.190");
    // Una sola indicació (n'hi ha de la PLACSP i de la PSCP) i una sola atribució.
    expect(document.querySelectorAll("[data-lots-pscp-note]").length).toBe(1);
    expect(text(document.querySelector("[data-lots-pscp-note]"))).toBe("Els imports sense font indicada són de la PLACSP; la resta, de la PSCP.");
    expect(document.querySelectorAll("[data-pscp-attribution]").length).toBe(1);
  });

  it("F-06e: un conflicte de suma a 7 lots diu el motiu i l'enllaç una sola vegada, a la indicació de la taula", async () => {
    const ids = ["l1", "l2", "l3", "l4", "l5", "l6", "l7"];
    const lots = ids.map((id, index) => lotOf(id, `Lot número ${index + 1}`));
    const sum = (id: string) => [id, observation({ status: "conflict", amount: "28799.70", amount_kind: "pbl_without_tax", failed_checks: ["lot_sum"] })];
    await renderWith(
      {
        ...field(null),
        lots_pbl_without_tax: Object.fromEntries(ids.map(sum)),
        lots_estimated_value: Object.fromEntries(ids.map((id) => [id, observation({ amount: "30000.00", amount_kind: "estimated_value" })])),
      },
      { lots, lot_count: 7 },
    );
    const body = text(document.body);
    expect(body.split("la suma dels imports dels lots publicats a la PSCP").length - 1).toBe(1);
    expect(document.querySelectorAll("[data-lots-sum-conflict]").length).toBe(1);
    expect(text(document.querySelector("[data-lots-sum-conflict]"))).toContain("PBL sense IVA: la suma");
    expect(document.querySelectorAll("[data-pscp-sum-conflict]").length).toBe(7);
    for (const cell of Array.from(document.querySelectorAll("[data-pscp-sum-conflict]"))) {
      expect(text(cell)).toBe("Pendent de verificació (discrepància entre fonts)");
    }
    // L'enllaç a la publicació: una sola vegada per al conflicte (els VEC accepted porten el seu).
    expect(document.querySelector("[data-lots-sum-conflict]")?.querySelectorAll("a").length).toBe(1);
    expect(screen.getAllByRole("link", { name: "Veure la publicació ↗" }).length).toBe(1);
  });

  it("F-06e: un conflicte propi del lot (no de suma) es queda a la cel·la, com ara", async () => {
    await renderWith(
      { ...field(null), lots_estimated_value: { l1: observation({ status: "conflict", amount_kind: "estimated_value", failed_checks: ["vec"] }) } },
      { lots: [lotOf("l1", "Lot u")], lot_count: 1 },
    );
    const cell = screen.getByText("Lot u").closest("tr")!.querySelector("[data-pscp-lot]");
    expect(text(cell?.querySelector("[data-pscp-notice]"))).toBe("No coincideix amb les dades de la PLACSP (VEC). · Veure la publicació ↗");
    expect(document.querySelector("[data-lots-sum-conflict]")).toBeNull();
  });

  it("F-06e: lots sense cap import ni cap observació (fora de Catalunya): la taula com abans, una sola columna «Import»", async () => {
    await renderWith(field(null), { lots: [lotOf("l1", "Lot u"), lotOf("l2", "Lot dos")], lot_count: 2 });
    const table = screen.getByText("Lot u").closest("table")!;
    const headers = Array.from(table.querySelectorAll("th")).map((th) => th.textContent);
    expect(headers).toContain("Import");
    expect(headers).not.toContain("VEC");
    expect(headers).not.toContain("PBL sense IVA");
    expect(table.querySelector("[data-lot-amount]")).toBeNull();
    for (const title of ["Lot u", "Lot dos"]) expect(text(screen.getByText(title).closest("tr"))).toContain(NOT_PUBLISHED);
    expect(document.querySelector("[data-lots-pscp-note]")).toBeNull();
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
  });

  it("F-06e: només observacions no_value nul·les: la taula d'abans, sense les tres columnes buides", async () => {
    await renderWith(
      {
        ...field(null, { l1: observation({ status: "no_value", amount: null, published_zero: false }) }),
        lots_estimated_value: { l1: observation({ status: "no_value", amount: null, published_zero: false, amount_kind: "estimated_value" }) },
      },
      { lots: [lotOf("l1", "Lot u")], lot_count: 1 },
    );
    const table = screen.getByText("Lot u").closest("table")!;
    const headers = Array.from(table.querySelectorAll("th")).map((th) => th.textContent);
    expect(headers).toContain("Import");
    expect(headers).not.toContain("VEC");
    expect(table.querySelector("[data-lot-amount]")).toBeNull();
    expect(text(screen.getByText("Lot u").closest("tr"))).toContain(NOT_PUBLISHED);
  });

  it("F-06e: un conflicte per un import germà no representable no diu que la suma no quadri", async () => {
    // L'API no dona `lot_sum` quan la guarda de suma porta `problem` (N-1): `failed_checks` buit.
    await renderWith(
      { ...field(null), lots_pbl_without_tax: { l1: observation({ status: "conflict", amount_kind: "pbl_without_tax", failed_checks: [] }) } },
      { lots: [lotOf("l1", "Lot u")], lot_count: 1 },
    );
    const cell = screen.getByText("Lot u").closest("tr")!.querySelector("[data-pscp-lot]");
    expect(text(cell?.querySelector("[data-pscp-notice]"))).toBe("No ha superat les comprovacions de coherència de la font. · Veure la publicació ↗");
    expect(text(document.body)).not.toContain("la suma dels imports");
    expect(document.querySelector("[data-lots-sum-conflict]")).toBeNull();
  });

  it("F-06e: un VEC de lot publicat a 0 a la PSCP diu el zero, mai un import; un no_value nul no diu res", async () => {
    await renderWith(
      {
        ...field(null),
        lots_estimated_value: { l1: observation({ status: "no_value", amount: "0.00", published_zero: true, amount_kind: "estimated_value" }) },
        lots_pbl_without_tax: { l1: observation({ status: "no_value", amount: null, published_zero: false, amount_kind: "pbl_without_tax" }) },
      },
      { lots: [lotOf("l1", "Lot u")], lot_count: 1 },
    );
    const row = screen.getByText("Lot u").closest("tr");
    const cells = Array.from(row!.querySelectorAll("[data-lot-amount]")).map((cell) => text(cell));
    expect(cells).toEqual(["Publicat a 0,00 € a la PSCP", `—${NOT_PUBLISHED}`, `—${NOT_PUBLISHED}`]);
    expect(document.querySelectorAll("[data-pscp-attribution]").length).toBe(1);
  });

  it("un lot amb PBL amb IVA de la PLACSP el conserva: cap segon «PBL amb IVA» de la PSCP ni atribució", async () => {
    const withPlacsp = { ...lotOf("l1", "Lot amb PLACSP"), base_budget: "1210.00" };
    await renderWith(field(null, { l1: observation({ amount: "32670.00" }) }), { lots: [withPlacsp], lot_count: 1 });
    const row = screen.getByText("Lot amb PLACSP").closest("tr");
    expect(text(row)).toContain("1.210,00 €");
    expect(row?.querySelector("[data-pscp-lot]")).toBeNull();
    expect(document.querySelector("[data-lots-pscp-note]")).toBeNull();
    expect(text(row)).not.toContain("32.670,00");
    expect(document.querySelector("[data-pscp-attribution]")).toBeNull();
  });

  it("zeros (19-10-2026): un PBL amb IVA de la PLACSP a 0,00 € no amaga la PSCP, ni al fet ni al lot", async () => {
    const zeroLot = { ...lotOf("l1", "Lot amb zero"), base_budget: "0.00" };
    const fact = await renderWith(field(observation(), { l1: observation({ amount: "32670.00" }) }), {
      base_budget: "0.00",
      lots: [zeroLot],
      lot_count: 1,
    });
    expect(text(fact)).toContain("247.121,63 €");
    expect(text(fact)).not.toContain("Publicat a 0,00 € a la PLACSP");
    const row = screen.getByText("Lot amb zero").closest("tr");
    expect(text(row?.querySelector("[data-pscp-lot]"))).toBe("32.670,00 € · PSCP ↗");
    expect(document.querySelector("[data-lots-pscp-note]")).not.toBeNull();
    // El 0,00 € de la PLACSP no es mostra com a import de cap cel·la.
    expect(row?.querySelector('[data-lot-amount="placsp"]')).toBeNull();
    expect(document.querySelector("[data-pscp-attribution]")).not.toBeNull();
  });
});
