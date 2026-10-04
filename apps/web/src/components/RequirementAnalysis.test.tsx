import { act, fireEvent, render, screen, within } from "@testing-library/react";
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
let role = "owner";
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { id: "m1", company_id: "c1", role, is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
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
    origin: "extracted" as const,
    archived_at: null,
    citations: [],
    timing: "unknown" as const,
    tender_id: "t1",
    tender_lot_id: null,
    category: "certification",
    obligation: "mandatory",
    routing: "compliance_dossier",
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
    expect(screen.getByText("Anàlisi del plec")).toBeTruthy();
    expect(screen.getByText("Extreu automàticament els requisits i els criteris dels plecs.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Inicia l'anàlisi" }));
    await flush();
    expect(confirmAnalysis).toHaveBeenCalledWith("c1", "q1");
    expect(track).toHaveBeenCalledWith(expect.objectContaining({ id: "j1", status: "accepted" }));
  });

  it("sense permís de confirmar: el botó es veu, desactivat; primer què fa i després per què no", async () => {
    get.mockResolvedValue({ ...limits, can_confirm: false });
    previewAnalysis.mockResolvedValue({ id: "q1", tender_id: "t1", status: "open", planning_version: "1", profile: "std", provider: "anthropic", model: "claude", force: false, input_fingerprint: "f", documents: [], skipped_documents: [], pages_total: 0, pages_in_plan: 0, pages_truncated: false, chunks: 0, chunks_to_send: 0, reusable_run_id: null, estimated_input_tokens: null, estimated_output_tokens: null, max_chunks: 40, allowed: true, blocking_reason: null, expires_at: "" });
    renderSection(null);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Analitza els plecs" }));
    await flush();
    const confirm = (await screen.findByRole("button", { name: "Inicia l'anàlisi" })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(confirmAnalysis).not.toHaveBeenCalled();
    const what = screen.getByText("Extreu automàticament els requisits i els criteris dels plecs.");
    const why = screen.getByText(/^Només els responsables de la licitació poden iniciar una anàlisi nova/);
    expect(what.compareDocumentPosition(why) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Ni vocabulari intern de rols ni «confirmar», que sonava a confirmar la revisió.
    expect(screen.queryByText(/Propietari|Administrador|Responsable de candidatures/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirma/ })).toBeNull();
  });

  it("amb una anàlisi feta: es diu que és una anàlisi nova i que la revisió no canvia", async () => {
    get.mockResolvedValue({ ...limits, can_confirm: false });
    previewAnalysis.mockResolvedValue({ id: "q2", tender_id: "t1", status: "open", planning_version: "1", profile: "std", provider: "anthropic", model: "claude", force: true, input_fingerprint: "f", documents: [], skipped_documents: [], pages_total: 0, pages_in_plan: 0, pages_truncated: false, chunks: 0, chunks_to_send: 0, reusable_run_id: null, estimated_input_tokens: null, estimated_output_tokens: null, max_chunks: 40, allowed: true, blocking_reason: null, expires_at: "" });
    renderSection(analysis());
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Torna a analitzar" }));
    await flush();
    expect(await screen.findByText("Nova anàlisi del plec")).toBeTruthy();
    expect(screen.getByText(/Torna a extreure automàticament .* La revisió de l'empresa no canvia\./)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Inicia l'anàlisi" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("amb anàlisi: pastilla d'estat real, requisits amb cita, pàgina i atribució IA; cap codi intern", async () => {
    const { container } = renderSection(analysis());
    await flush();
    const pills = [...container.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pills.some((pill) => /Anàlisi completa · 2 requisits/.test(pill) && /\[ready\]$/.test(pill))).toBe(true);
    expect(screen.getByText("Certificat ISO 14001 en vigor")).toBeTruthy();
    expect(container.textContent).toContain("Plec administratiu · p. 4 · versió del text 1");
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
    fireEvent.click(screen.getByRole("button", { name: /Incidències de l'extracció/ }));
    expect(screen.queryByText("Certificat ISO 14001 en vigor")).toBeNull();
    expect(screen.getByText("Sublímit per víctima de 150.000 €")).toBeTruthy();
  });

  it("els requisits es llegeixen, pero aqui ja no es revisen: cap boto d'estat", async () => {
    renderSection(analysis());
    await flush();

    for (const title of ["Certificat ISO 14001 en vigor", "Sublímit per víctima de 150.000 €", "Memòria tècnica"]) {
      const row = screen.getByText(title).closest("li")!;
      const labels = [...row.querySelectorAll("button")].map((b) => b.textContent?.trim());
      for (const legacy of ["Verificat", "Cal revisar", "Per revisar", "Descartat", "Revisat"]) {
        expect(labels).not.toContain(legacy);
      }
    }
    // L'estat que va deixar l'anàlisi es continua veient, nomes com a pastilla.
    const r2 = screen.getByText("Sublímit per víctima de 150.000 €").closest("li")!;
    expect(r2.querySelector("[data-level]")).not.toBeNull();
    expect(screen.getByText(/La revisió es gestiona des de la vista de revisió\./)).toBeTruthy();
  });

  it("la revisio porta a la vista canonica /revisio", async () => {
    renderSection(analysis());
    await flush();

    const link = screen.getByRole("link", { name: "Obre la revisió" });
    expect(link.getAttribute("href")).toBe("/licitacions/t1/revisio");
  });

  it("una anàlisi incompleta es diu en llengua de producte; el diagnòstic cru només al detall tècnic", async () => {
    const raw = "1 de 22 chunks fallits (schema_invalid: 1). Darrer error: schema_invalid: La sortida no compleix el contracte del perfil (2 errors).";
    const { container } = renderSection(analysis({
      run: run({ status: "partial", error_code: "some_chunks_failed", error_message: raw, chunks_failed: 1,
                 requirements_extracted: 214, award_criteria_extracted: 14 }),
    }));
    await flush();

    expect(screen.getByText("Anàlisi incompleta: una part del plec no s'ha pogut processar automàticament.")).toBeTruthy();
    expect(screen.getByText("La revisió pot incorporar contingut revisat manualment.")).toBeTruthy();
    const visible = container.cloneNode(true) as HTMLElement;
    visible.querySelectorAll("details").forEach((element) => element.remove());
    expect(visible.textContent).not.toMatch(/chunk|schema_invalid|contracte del perfil/);
    const technical = [...container.querySelectorAll("details")].find((d) => d.textContent?.includes("Detall tècnic") && d.textContent?.includes(raw));
    expect(technical).toBeTruthy();
    // Les xifres de l'anàlisi automàtica es diuen com el que són.
    expect(screen.getByText("Anàlisi automàtica")).toBeTruthy();
    expect(screen.getByText("Requisits extrets")).toBeTruthy();
    expect(screen.getByText("Criteris detectats")).toBeTruthy();
  });

  it("a la llista d'extrets, la revisió és un enllaç discret, no una segona crida principal", async () => {
    renderSection(analysis());
    await flush();
    const link = screen.getByRole("link", { name: "Obre la revisió" });
    expect(link.className).not.toMatch(/bg-brand/);
    expect(screen.getByText(/^Requisits extrets \(/)).toBeTruthy();
  });

  it("res de la llista no pot fer el POST antic de revisio", async () => {
    post.mockImplementation(async (path: string) => {
      if (/^\/(requirements|award-criteria)\/[^/]+\/review$/.test(path)) {
        throw new Error(`POST antic de revisio cridat: ${path}`);
      }
      return {};
    });
    renderSection(analysis(), { editable: true });
    await flush();

    const list = screen.getByText("Certificat ISO 14001 en vigor").closest("ul")!.parentElement!.parentElement!;
    for (const button of [...list.querySelectorAll("li button")]) {
      fireEvent.click(button);
      await flush();
    }
    fireEvent.click(screen.getByRole("link", { name: "Obre la revisió" }));
    await flush();

    expect(post.mock.calls.filter(([path]) => /\/review$/.test(String(path)))).toEqual([]);
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


/* ----- Requisits escrits a mà (S3-S5) -------------------------------------- */

describe("requisits escrits a mà", () => {
  function manual(overrides: Partial<Requirement> = {}): Requirement {
    return {
      ...requirement(),
      id: "manual-1",
      run_id: null,
      origin: "manual",
      archived_at: null,
      citations: [],
      confidence: null,
      title: "Assegurança de responsabilitat civil",
      evidence: [],
      ...overrides,
    };
  }

  it("sense licitació editable no s'ofereix escriure'n cap", async () => {
    renderSection({ run: run(), requirements: [manual()], award_criteria: [], is_stale: false });
    await flush();

    expect(screen.queryByRole("button", { name: /Afegeix un requisit/ })).toBeNull();
  });

  it("qui no prepara la candidatura tampoc no n'escriu", async () => {
    role = "viewer";
    renderSection({ run: run(), requirements: [manual()], award_criteria: [], is_stale: false }, { editable: true });
    await flush();

    expect(screen.queryByRole("button", { name: /Afegeix un requisit/ })).toBeNull();
    role = "owner";
  });

  it("un requisit manual no porta atribució d'IA", async () => {
    renderSection({ run: run(), requirements: [manual()], award_criteria: [], is_stale: false }, { editable: true });
    await flush();

    expect(screen.getByText(/Escrit a mà/)).toBeTruthy();
    expect(screen.queryByText(/Proposat per l'anàlisi/)).toBeNull();
    expect(screen.queryByText(/confiança/i)).toBeNull();
  });

  it("un requisit extret manté només la revisió: editar-lo seria reescriure una run", async () => {
    renderSection({
          run: run(),
          requirements: [requirement()],
          award_criteria: [],
          is_stale: false,
        }, { editable: true });
    await flush();

    expect(screen.queryByRole("button", { name: "Edita" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Arxiva" })).toBeNull();
    expect(screen.getByText(/Proposat per l'anàlisi/)).toBeTruthy();
  });

  it("escriure'n un el crea per la ruta de l'empresa", async () => {
    renderSection({ run: run(), requirements: [], award_criteria: [], is_stale: false }, { editable: true });
    await flush();

    fireEvent.click(screen.getByRole("button", { name: /Afegeix un requisit/ }));
    await flush();
    fireEvent.change(screen.getByLabelText(/Què exigeix el plec/), {
      target: { value: "Assegurança de 600.000 €" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Desa" }));
    await flush();

    expect(post).toHaveBeenCalled();
    const [path, body] = post.mock.calls.at(-1) as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/tenders/t1/requirements");
    expect(body.title).toBe("Assegurança de 600.000 €");
    // Cap camp que digui com ha nascut la fila.
    for (const forbidden of ["origin", "run_id", "confidence", "archived_at"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("arxivar-lo no l'esborra: es demana l'arxivat", async () => {
    renderSection({ run: run(), requirements: [manual()], award_criteria: [], is_stale: false }, { editable: true });
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Arxiva" }));
    await flush();

    const [path] = post.mock.calls.at(-1) as [string];
    expect(path).toBe("/companies/c1/tenders/t1/requirements/manual-1/archive");
    expect(path).not.toContain("delete");
  });

  it("les cites es veuen amb la versió i la pàgina que citaven", async () => {
    render(
      <RequirementAnalysisSection
        tenderId="t1"
        companyId="c1"
        analysis={{
          run: run(),
          requirements: [
            manual({
              citations: [
                {
                  id: "ref-1",
                  ref_kind: "document_file",
                  document_id: "d1",
                  document_name: "Preus.xlsx",
                  file_id: "f1",
                  version_number: 1,
                  content_sha256: "a".repeat(64),
                  extraction_id: null,
                  page_id: null,
                  page_number: null,
                  location_label: "Full Càlculs, fila 42",
                  quote: null,
                  char_start: null,
                  char_end: null,
                  created_at: "2026-09-22T10:00:00Z",
                },
              ],
            }),
          ],
          award_criteria: [],
          is_stale: false,
        }}
        loading={false}
        error={null}
        onReload={reload}
        editable
      />,
    );
    await flush();

    expect(screen.getByText(/Preus.xlsx/)).toBeTruthy();
    expect(screen.getByText(/Full Càlculs, fila 42/)).toBeTruthy();
  });

  describe("senyals de l'extracció, no de la revisió", () => {
    it("les pastilles de fila diuen «Extracció …», de contorn, i mai el vocabulari de la revisió", async () => {
      const { container } = renderSection(analysis({
        requirements: [
          requirement(),
          requirement({ id: "r2", title: "Sublímit per víctima de 150.000 €", review_status: "needs_review" }),
          requirement({ id: "r4", title: "Assegurança validada abans", review_status: "verified" }),
          requirement({ id: "r5", title: "Fila descartada abans", review_status: "rejected" }),
        ],
      }));
      await flush();

      const pill = (title: string) => {
        const row = screen.getByText(title).closest("li")!;
        return [...row.querySelectorAll("[data-level]")].at(-1) as HTMLElement;
      };
      expect(pill("Certificat ISO 14001 en vigor").textContent).toBe("Extracció sense validar");
      expect(pill("Sublímit per víctima de 150.000 €").textContent).toBe("Extracció amb incidència");
      expect(pill("Assegurança validada abans").textContent).toBe("Extracció validada");
      expect(pill("Fila descartada abans").textContent).toBe("Extracció descartada");
      expect(pill("Sublímit per víctima de 150.000 €").getAttribute("data-outline")).toBe("true");

      const list = screen.getByText(/^Requisits extrets \(/).closest("section, div")!.parentElement as HTMLElement;
      for (const canonical of ["Cal revisar", "Pendent de revisar", "Verificat", "No revisat", "Per revisar"]) {
        expect(within(list).queryByText(canonical)).toBeNull();
      }
      expect(container.textContent).not.toMatch(/\bCal revisar\b|\bNo revisat\b/);
    });

    it("els filtres diuen què compten a l'extracció, amb els mateixos recomptes", async () => {
      renderSection(analysis());
      await flush();

      const group = screen.getByRole("group", { name: "Filtres de requisits" });
      const labels = within(group).getAllByRole("button").map((b) => b.textContent);
      // r2 marcat per l'anàlisi + r3 de confiança baixa = 2; r3 és l'únic «puntuable».
      expect(labels).toEqual(["Tots3", "Obligatoris2", "Incidències de l'extracció2", "Puntuables segons l'extracció1"]);
      expect(within(group).queryByText("Per revisar")).toBeNull();
      expect(within(group).queryByText(/^Puntuables$/)).toBeNull();
    });

    it("l'extracció no repeteix el resum de la revisió: només un enllaç discret", async () => {
      renderSection(analysis());
      await flush();

      expect(screen.queryByLabelText("Resum de la revisió")).toBeNull();
      const link = screen.getByRole("link", { name: "Obre la revisió" });
      expect(link.className).not.toMatch(/bg-brand/);
    });
  });

  describe("fragments fallits", () => {
    it("un fragment: «1 fallit»", async () => {
      renderSection(analysis({ run: run({ status: "partial", chunks_total: 22, chunks_completed: 21, chunks_failed: 1 }) }));
      await flush();
      expect(screen.getByText(/· 1 fallit$/)).toBeTruthy();
      expect(screen.queryByText(/1 fallits/)).toBeNull();
    });

    it("diversos: «N fallits»", async () => {
      renderSection(analysis({ run: run({ status: "partial", chunks_total: 22, chunks_completed: 19, chunks_failed: 3 }) }));
      await flush();
      expect(screen.getByText(/· 3 fallits$/)).toBeTruthy();
    });
  });
});
