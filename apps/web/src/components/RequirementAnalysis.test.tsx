import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisQuote, JobRequest } from "@/lib/jobs";
import type { ExtractionRun, Requirement, RequirementAnalysis } from "@/lib/requirements";

const get = vi.fn();
const post = vi.fn();
const previewAnalysis = vi.fn();
const confirmAnalysis = vi.fn();
const track = vi.fn();
let jobRequest: JobRequest | null = null;
let onFinishedRef: ((request: JobRequest) => void | Promise<void>) | null = null;
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) } };
});
vi.mock("@/lib/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/jobs")>();
  return {
    ...actual,
    useJobRequest: (_c: string | null, _t: string, _k: string, onFinished: (request: JobRequest) => void | Promise<void>) => {
      onFinishedRef = onFinished;
      return { request: jobRequest, active: jobRequest?.status === "accepted", track };
    },
    previewAnalysis: (...args: unknown[]) => previewAnalysis(...args),
    confirmAnalysis: (...args: unknown[]) => confirmAnalysis(...args),
  };
});

import { RequirementAnalysisSection } from "./RequirementAnalysis";

const limits = { max_chunks_per_run: 40, max_active_per_company: 2, active_now: 0, can_confirm: true, can_process_documents: true, confirm_roles: ["owner", "admin"] };

function run(overrides: Partial<ExtractionRun> = {}): ExtractionRun {
  return {
    id: "x1",
    tender_id: "t1",
    status: "completed",
    is_current: true,
    input_fingerprint: "f",
    extractor_version: "tender-requirements-v1",
    prompt_version: "requirements-v1",
    schema_version: "1",
    provider: "anthropic",
    model: "claude",
    analysis_basis: "real",
    started_at: "2026-09-09T10:00:00Z",
    completed_at: "2026-09-09T10:05:00Z",
    documents_analyzed: 2,
    pages_analyzed: 40,
    chunks_total: 14,
    chunks_completed: 14,
    chunks_failed: 0,
    requirements_extracted: 2,
    requirements_rejected: 0,
    award_criteria_extracted: 1,
    evidence_rejected: 0,
    validation_errors: 0,
    input_tokens: 1000,
    output_tokens: 200,
    estimated_cost: null,
    duration_ms: 4000,
    skipped_documents: null,
    error_code: null,
    error_message: null,
    ...overrides,
  };
}

function requirement(overrides: Partial<Requirement> = {}): Requirement {
  return {
    id: "r1",
    run_id: "x1",
    tender_id: "t1",
    tender_lot_id: null,
    category: "certification",
    obligation: "mandatory",
    operator: null,
    scope: "tender",
    title: "Certificat ISO 14001 en vigor",
    description: null,
    value_json: null,
    raw_lot_labels: null,
    confidence: "high",
    review_status: "unreviewed",
    review_note: null,
    manual_override_json: null,
    reviewed_at: null,
    has_conflict: false,
    conflict_group: null,
    created_at: "2026-09-09T10:05:00Z",
    evidence: [
      { id: "e1", extraction_id: "ex1", page_id: "p4", tender_document_id: "d1", page_number: 4, quote: "Sistema de gestió ambiental certificat ISO 14001", char_start: null, char_end: null, section: null, evidence_role: "primary", extraction_revision: 1, document_name: "Plec administratiu" },
    ],
    ...overrides,
  };
}

function analysis(overrides: Partial<RequirementAnalysis> = {}): RequirementAnalysis {
  return {
    run: run(),
    requirements: [
      requirement(),
      requirement({ id: "r2", category: "insurance", title: "Sublímit per víctima de 150.000 €", confidence: "medium", review_status: "needs_review", obligation: "mandatory" }),
      requirement({ id: "r3", category: "technical_requirement", title: "Memòria tècnica", obligation: "scored", confidence: "low", evidence: [] }),
    ],
    award_criteria: [],
    is_stale: false,
    ...overrides,
  };
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const reload = vi.fn(async () => undefined);

function renderSection(value: RequirementAnalysis | null, extra: Partial<React.ComponentProps<typeof RequirementAnalysisSection>> = {}) {
  return render(
    <RequirementAnalysisSection tenderId="t1" companyId="c1" analysis={value} loading={false} error={null} onReload={reload} {...extra} />,
  );
}

describe("RequirementAnalysisSection", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    previewAnalysis.mockReset();
    confirmAnalysis.mockReset();
    track.mockReset();
    reload.mockClear();
    jobRequest = null;
    get.mockResolvedValue(limits);
  });

  it("sense anàlisi: B3 diu que «0 bloquejos» no vol dir res i l'acció passa pel preview, mai directament", async () => {
    const quote: AnalysisQuote = { id: "q1", tender_id: "t1", status: "open", planning_version: "1", profile: "std", provider: "anthropic", model: "claude", force: false, input_fingerprint: "f", documents: [{ tender_document_id: "d1", name: "Plec administratiu", document_type: "administrative_terms", pages: 12, chars: 20000 }], skipped_documents: [], pages_total: 12, pages_in_plan: 12, pages_truncated: false, chunks: 4, chunks_to_send: 4, reusable_run_id: null, estimated_input_tokens: null, estimated_output_tokens: null, max_chunks: 40, allowed: true, blocking_reason: null, expires_at: "2026-09-17T10:00:00Z" };
    previewAnalysis.mockResolvedValue(quote);
    confirmAnalysis.mockResolvedValue({ id: "j1", job_type: "analyze_tender_requirements", tender_id: "t1", status: "accepted", outcome: null, reused_existing: false, result_run_id: null, error_code: null, requested_at: "", finished_at: null, progress: null, quote_id: "q1" });
    renderSection(null);
    expect(await screen.findByRole("heading", { name: "Aquest plec encara no s'ha llegit" })).toBeTruthy();
    expect(screen.getByText(/«0 bloquejos» aquí no vol dir que no n'hi hagi/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Analitza els plecs" }));
    await flush();
    expect(previewAnalysis).toHaveBeenCalledWith("c1", "t1", false);
    expect(confirmAnalysis).not.toHaveBeenCalled();
    expect(await screen.findByText("Aquest és el volum")).toBeTruthy();
    expect(screen.getByText("Plec administratiu")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirma i analitza" }));
    await flush();
    expect(confirmAnalysis).toHaveBeenCalledWith("c1", "q1");
    expect(track).toHaveBeenCalledWith(expect.objectContaining({ id: "j1", status: "accepted" }));
  });

  it("sense permís de confirmar: el botó es veu, desactivat, amb qui sí que pot", async () => {
    get.mockResolvedValue({ ...limits, can_confirm: false });
    previewAnalysis.mockResolvedValue({ id: "q1", tender_id: "t1", status: "open", planning_version: "1", profile: "std", provider: "anthropic", model: "claude", force: false, input_fingerprint: "f", documents: [], skipped_documents: [], pages_total: 0, pages_in_plan: 0, pages_truncated: false, chunks: 0, chunks_to_send: 0, reusable_run_id: null, estimated_input_tokens: null, estimated_output_tokens: null, max_chunks: 40, allowed: true, blocking_reason: null, expires_at: "" });
    renderSection(null);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Analitza els plecs" }));
    await flush();
    const confirm = (await screen.findByRole("button", { name: "Confirma i analitza" })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText(/Només .*Propietari.*pot confirmar l'anàlisi\./)).toBeTruthy();
  });

  it("amb anàlisi: pastilla d'estat real, requisits amb cita, pàgina i atribució IA; cap codi intern", async () => {
    const { container } = renderSection(analysis());
    await flush();
    const pills = [...container.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pills.some((pill) => /Anàlisi completa · 2 requisits/.test(pill) && /\[ready\]$/.test(pill))).toBe(true);
    expect(screen.getByText("Certificat ISO 14001 en vigor")).toBeTruthy();
    expect(container.textContent).toContain("Plec administratiu · p. 4 · revisió 1");
    expect(screen.getAllByRole("link", { name: "Obre la pàgina 4 del plec →" })[0].getAttribute("href")).toBe("/licitacions/t1/documents/d1?page=4&extraction=ex1");
    expect(container.textContent).toContain("confiança alta");
    expect(container.querySelector("[data-kind='ai']")).not.toBeNull();
    expect(pills).toContain("Sense cita al plec[attention]");
    // Les versions del motor només viuen dins del detall tècnic plegat.
    const visible = container.cloneNode(true) as HTMLElement;
    visible.querySelectorAll("details").forEach((element) => element.remove());
    expect(visible.textContent).not.toMatch(/requirements-v1|tender-requirements-v1/);
    expect([...container.querySelectorAll("details")].some((d) => d.textContent?.includes("tender-requirements-v1"))).toBe(true);
    expect(container.textContent).not.toMatch(/\{"/);
    // Filtres de recompte: els que demanen revisió es poden aïllar.
    fireEvent.click(screen.getByRole("button", { name: /Per revisar/ }));
    expect(screen.queryByText("Certificat ISO 14001 en vigor")).toBeNull();
    expect(screen.getByText("Sublímit per víctima de 150.000 €")).toBeTruthy();
  });

  it("revisar un requisit fa el POST de sempre i recarrega", async () => {
    post.mockResolvedValue({});
    renderSection(analysis());
    await flush();
    const row = screen.getByText("Certificat ISO 14001 en vigor").closest("li")!;
    fireEvent.click([...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Verificat")!);
    await flush();
    expect(post).toHaveBeenCalledWith("/requirements/r1/review", { review_status: "verified" });
    expect(reload).toHaveBeenCalled();
  });

  it("anàlisi parcial: mai «a punt», i els documents omesos pel nom; simulació: mai «a punt»", async () => {
    const { container, unmount } = renderSection(analysis({ run: run({ status: "partial", skipped_documents: [{ tender_document_id: "d2", document_name: "Plànols.pdf", document_type: "other", reason: "document_without_text" }] }) }));
    await flush();
    let header = container.querySelector("header")!;
    let pill = header.querySelector("[data-level]")!;
    expect(pill.getAttribute("data-level")).toBe("attention");
    expect(pill.textContent).toContain("Anàlisi parcial");
    expect(container.textContent).toContain("Plànols.pdf");
    expect(container.textContent).toContain("Processat però sense text");
    unmount();
    const second = renderSection(analysis({ run: run({ analysis_basis: "simulation" }) }));
    await flush();
    header = second.container.querySelector("header")!;
    pill = header.querySelector("[data-level]")!;
    expect(pill.getAttribute("data-level")).not.toBe("ready");
    expect(pill.textContent).toContain("Simulació de desenvolupament");
  });

  it("feina fallada: la frase del diccionari, l'anàlisi anterior intacta i el codi només al detall tècnic", async () => {
    jobRequest = { id: "j9", job_type: "analyze_tender_requirements", tender_id: "t1", status: "failed", outcome: null, reused_existing: false, result_run_id: null, error_code: "analysis_too_large", requested_at: "", finished_at: "2026-09-15T10:42:00Z", progress: null };
    const { container } = renderSection(analysis());
    await flush();
    await act(async () => { await onFinishedRef?.(jobRequest!); });
    expect(screen.getByRole("heading", { name: "El plec és massa gran per llegir-lo d'un cop" })).toBeTruthy();
    expect(screen.getByText("Certificat ISO 14001 en vigor")).toBeTruthy();
    const details = [...container.querySelectorAll("details")].find((d) => d.textContent?.includes("analysis_too_large"));
    expect(details).toBeTruthy();
    expect(screen.getByRole("heading", { name: /massa gran/ }).textContent).not.toContain("analysis_too_large");
  });

  it("feina en curs: bloc en curs amb progrés i el botó desactivat", async () => {
    jobRequest = { id: "j2", job_type: "analyze_tender_requirements", tender_id: "t1", status: "accepted", outcome: null, reused_existing: false, result_run_id: null, error_code: null, requested_at: "", finished_at: null, progress: { done: 5, total: 18 } };
    const { container } = renderSection(analysis());
    await flush();
    expect(container.textContent).toContain("5 de 18");
    expect(container.textContent).toContain("Pots tancar la pàgina");
    expect((screen.getByRole("button", { name: /Torna a analitzar|Analitza/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
