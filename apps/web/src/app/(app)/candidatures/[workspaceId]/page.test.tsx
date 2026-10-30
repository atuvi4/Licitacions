import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readinessFixture } from "@/test/readiness-fixture";
import { analysisFixture } from "@/test/analysis-fixture";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

const get = vi.fn();
const readiness = vi.fn();
const analysisCurrent = vi.fn();
const post = vi.fn();
const push = vi.fn();
const replace = vi.fn();
let search = "";
// La preparació final és un recurs a part: si totes dues lectures anessin al
// mateix mock, els `toHaveBeenCalledTimes` d'aquest fitxer deixarien de parlar
// de la candidatura i les cues de `mockResolvedValueOnce` es barrejarien.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const route = (path: unknown, ...rest: unknown[]) =>
    typeof path === "string" && path.includes("/final-readiness")
      ? readiness(path, ...rest)
      : typeof path === "string" && path.includes("/analysis/current")
        ? analysisCurrent(path, ...rest)
        : get(path, ...rest);
  return { ...actual, api: { ...actual.api, get: route, post: (...args: unknown[]) => post(...args) } };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" } }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ workspaceId: "w1" }),
  useRouter: () => ({ push, replace, refresh: vi.fn() }),
  usePathname: () => "/candidatures/w1",
  useSearchParams: () => new URLSearchParams(search),
}));
vi.mock("@/components/WorkspaceDossier", () => ({
  DossierSection: ({ onChange, onAdvanced }: { onChange?: () => Promise<void>; onAdvanced?: () => Promise<void> }) => (
    <div data-testid="dossier">
      Dossier (secció actual)
      <button type="button" onClick={() => void onChange?.()}>simula desat del dossier</button>
      <button type="button" onClick={() => void onAdvanced?.()}>simula advance acceptat</button>
    </div>
  ),
}));
vi.mock("@/components/SubmissionPackage", () => ({ SubmissionPackageSection: () => <div data-testid="package">Paquet (secció actual)</div> }));
const leaveGuard = vi.fn(async () => true);
const economicGuard = vi.fn(async () => true);
vi.mock("@/components/EconomicOfferSection", () => ({
  EconomicOfferSection: ({ registerLeaveGuard }: { registerLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void }) => (
    <div data-testid="oferta">
      Oferta econòmica (secció)
      <button type="button" onClick={() => registerLeaveGuard?.(economicGuard)}>oferta amb canvis</button>
    </div>
  ),
}));
vi.mock("@/components/TechnicalSolutionSection", () => ({
  TechnicalSolutionSection: ({ registerLeaveGuard }: { registerLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void }) => (
    <div data-testid="solucio">
      Solució tècnica (secció)
      <button type="button" onClick={() => registerLeaveGuard?.(leaveGuard)}>simula canvis sense desar</button>
      <button type="button" onClick={() => registerLeaveGuard?.(null)}>simula desat</button>
    </div>
  ),
}));
vi.mock("@/components/WorkPlanDeadlines", () => ({ WorkPlanDeadlines: () => <div data-testid="deadlines">Terminis del plec (secció)</div> }));
// Espies de propietat: el mateix estat d'anàlisi ha d'arribar a la targeta i al panell
// (l'identificador surt com a atribut, mai com a text visible).
vi.mock("@/components/tender-analysis/DecisionAnalysisCard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/tender-analysis/DecisionAnalysisCard")>();
  return {
    DecisionAnalysisCard: (props: { state: { kind: string; run?: { id: string } } }) => (
      <div data-testid="card-spy" data-kind={props.state.kind} data-run-id={props.state.run?.id ?? ""}>
        <actual.DecisionAnalysisCard {...(props as Parameters<typeof actual.DecisionAnalysisCard>[0])} />
      </div>
    ),
  };
});
vi.mock("@/components/WorkspaceDecision", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/WorkspaceDecision")>();
  return {
    ...actual,
    DecisionPanel: (props: Parameters<typeof actual.DecisionPanel>[0]) => (
      <div data-testid="panel-spy" data-kind={props.analysis?.kind ?? ""} data-run-id={(props.analysis && "run" in props.analysis ? props.analysis.run.id : "")}>
        <actual.DecisionPanel {...props} />
      </div>
    ),
  };
});
vi.mock("@/components/WorkspaceWork", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/WorkspaceWork")>();
  return {
    ...actual,
    // El tauler dels sobres fa les seves lectures (compliment, solucions, oferta, dossier): aquí només compta
    // que hi sigui, i els recomptes de lectures d'aquest fitxer continuen parlant de la candidatura.
    PrepareOverview: () => <div data-testid="prepare-overview">Tauler dels sobres</div>,
    ChecklistSection: () => <div data-testid="checklist">Checklist (secció actual)</div>,
    TasksSection: () => <div data-testid="tasks">Tasques (secció actual)</div>,
    NotesSection: () => <div data-testid="notes">Notes (secció actual)</div>,
  };
});

import { ApiError } from "@/lib/api";

import WorkspacePage from "./page";

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void };

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

function step(name: string) {
  return screen.getByRole("link", { name: new RegExp(`^${name}`) }) as HTMLAnchorElement;
}

describe("Candidatura · stepper i Decidir", () => {
  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    analysisCurrent.mockReset();
    analysisCurrent.mockRejectedValue(new ApiError(404, "not_found", "Not found"));
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    search = "";
  });

  it("primera càrrega: esquelet de decisió; després, Decidir amb la pregunta", async () => {
    let deferred: Deferred | null = null;
    get.mockImplementation(() => new Promise((resolve, reject) => { deferred = { resolve, reject }; }));
    const { container } = render(<WorkspacePage />);
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(screen.queryByText("Ens presentem a aquesta licitació?")).toBeNull();
    await act(async () => { deferred!.resolve(workspaceFixture()); });
    await flush();
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Obra civil i estesa de fibra òptica");
    expect(container.textContent).toContain("Diputació Demo");
    expect(container.textContent).toContain("DIP-2026-077");
  });

  it("el pas per defecte surt de l'estat real i l'enllaç de cada pas porta ?pas=", async () => {
    get.mockResolvedValue(workspaceFixture({ status: "preparing_bid", human_decision: "go", latest_decision: decisionFixture(), decisions: [decisionFixture()] }));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("dossier")).toBeTruthy();
    // El pla de treball ja no comparteix pantalla amb el dossier: viu a la seva
    // àrea. Sense `area`, Preparar obre el dossier, que és el que ensenyava.
    expect(screen.queryByTestId("checklist")).toBeNull();
    expect(screen.queryByText("Ens presentem a aquesta licitació?")).toBeNull();
    expect(step("Preparar").getAttribute("aria-current")).toBe("step");
    expect(step("Decidir").getAttribute("aria-current")).toBeNull();
    expect(step("Decidir").getAttribute("href")).toBe("/candidatures/w1?pas=decidir");
    expect(step("Tancar").getAttribute("href")).toBe("/candidatures/w1?pas=tancar");
    expect(step("Decidir").closest("[data-state]")?.getAttribute("data-state")).toBe("done");
    expect(step("Preparar").closest("[data-state]")?.getAttribute("data-state")).toBe("on");
    // La primera càrrega fixa el pas a la URL perquè un refresc hi torni.
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/candidatures/w1?pas=preparar", { scroll: false });
    expect(push).not.toHaveBeenCalled();
  });

  it("?pas=tancar directe obre Tancar encara que l'estat real sigui Decidir", async () => {
    search = "pas=tancar";
    get.mockResolvedValue(workspaceFixture());
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("package")).toBeTruthy();
    expect(screen.queryByText("Ens presentem a aquesta licitació?")).toBeNull();
    expect(step("Tancar").getAttribute("aria-current")).toBe("step");
    // El progrés real no canvia perquè es miri un altre pas.
    expect(step("Decidir").closest("[data-state]")?.getAttribute("data-state")).toBe("on");
    expect(step("Tancar").closest("[data-state]")?.getAttribute("data-state")).toBe("todo");
    expect(replace).not.toHaveBeenCalled();
  });

  it("un ?pas= desconegut cau al pas de l'estat", async () => {
    search = "pas=foo";
    get.mockResolvedValue(workspaceFixture({ status: "ready_for_final_review" }));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("package")).toBeTruthy();
  });

  it("un sol bàner, el més greu primer, amb la resta de motius a dins", async () => {
    get.mockResolvedValue(
      workspaceFixture({
        tender_actionable: false,
        deadline_passed: true,
        days_remaining: -3,
        go_requires_reason: true,
      }),
    );
    const { container } = render(<WorkspacePage />);
    await flush();
    const banners = container.querySelectorAll("[role='status'][data-level]");
    expect(banners).toHaveLength(1);
    expect(banners[0].getAttribute("data-level")).toBe("blocking");
    expect(banners[0].textContent).toContain("Anul·lada");
    expect(banners[0].textContent).toContain("El termini de presentació ja ha passat");
  });

  it("senyals del compliment anterior: cap bàner (ni Bloqueig, ni Avisos, ni Anàlisi endarrerida)", async () => {
    const decision = decisionFixture();
    get.mockResolvedValue(
      workspaceFixture({
        status: "go_approved",
        human_decision: "go",
        latest_decision: decision,
        decisions: [decision],
        analysis_outdated: true,
        outdated_reasons: ["compliance_run_changed"],
        counters: { ...workspaceFixture().counters, confirmed_blockers: 2 },
        open_warnings: ["confirmed_blockers", "outdated_analysis", "mandatory_unknown"],
      }),
    );
    search = "pas=decidir";
    const { container } = render(<WorkspacePage />);
    await flush();
    expect(container.querySelector("[role='status'][data-level]")).toBeNull();
    expect(container.textContent).toContain("Decisió vigent");
    expect(container.textContent).toContain("Anna Puig");
    expect(post).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(1);
  });

  const staleWorkspace = (overrides: Parameters<typeof workspaceFixture>[0] = {}) =>
    workspaceFixture({
      eligibility: "no_go",
      analysis_basis: "simulation",
      open_warnings: ["deadline_passed"],
      counters: { ...workspaceFixture().counters, confirmed_blockers: 3, potential_blockers: 1 },
      ...overrides,
    });
  const contextTexts = (container: HTMLElement) =>
    [...container.querySelectorAll("dl")].flatMap((dl) =>
      [...dl.querySelectorAll("dt, dd")].map((el) => `${el.tagName}:${el.textContent}`),
    );

  it("Decidir: el panell de context no té cap fila «Recomanació»; només Encaix", async () => {
    get.mockResolvedValue(workspaceFixture({ recommendation: "go_candidate", eligibility: "eligible" }));
    search = "pas=decidir";
    const { container } = render(<WorkspacePage />);
    await flush();
    const labels = [...container.querySelectorAll("dt")].map((dt) => dt.textContent);
    expect(labels).toContain("Encaix");
    expect(labels).not.toContain("Elegibilitat");
    expect(labels).not.toContain("Recomanació");
  });

  it("Decidir: sense «Què bloqueja», Elegibilitat, Anàlisi ni enllaços de compliment encara que les dades hi siguin", async () => {
    get.mockResolvedValue(staleWorkspace());
    search = "pas=decidir";
    const { container } = render(<WorkspacePage />);
    await flush();
    const labels = [...container.querySelectorAll("dt")].map((dt) => dt.textContent);
    expect(labels).not.toContain("Elegibilitat");
    expect(labels).not.toContain("Anàlisi");
    expect(container.textContent).not.toContain("Què bloqueja");
    expect(container.textContent).not.toContain("Veure el compliment detallat");
    expect(container.textContent).not.toContain("Analitza el plec");
  });

  it("Decidir: canviar eligibility i analysis_basis no canvia el panell de context", async () => {
    search = "pas=decidir";
    get.mockResolvedValueOnce(staleWorkspace());
    const first = render(<WorkspacePage />);
    await flush();
    const before = contextTexts(first.container);
    first.unmount();
    get.mockResolvedValueOnce(staleWorkspace({ eligibility: "eligible", analysis_basis: "real", analysis_outdated: true }));
    const second = render(<WorkspacePage />);
    await flush();
    expect(before.length).toBeGreaterThan(0);
    expect(contextTexts(second.container)).toEqual(before);
  });

  it("Decidir: Encaix surt només de prefit_score", async () => {
    search = "pas=decidir";
    get.mockResolvedValueOnce(workspaceFixture({ prefit_score: 72 }));
    const first = render(<WorkspacePage />);
    await flush();
    expect(first.container.textContent).toContain("72 sobre 100");
    first.unmount();
    get.mockResolvedValueOnce(workspaceFixture({ prefit_score: null }));
    const second = render(<WorkspacePage />);
    await flush();
    const dt = [...second.container.querySelectorAll("dt")].find((el) => el.textContent === "Encaix");
    expect(dt?.nextElementSibling?.textContent).toBe("Sense càlcul");
  });

  it("Decidir: la fila Lot només hi és amb lot_number", async () => {
    search = "pas=decidir";
    get.mockResolvedValueOnce(workspaceFixture({ lot_number: 2, lot_title: "Fibra" }));
    const first = render(<WorkspacePage />);
    await flush();
    const dt = [...first.container.querySelectorAll("dt")].find((el) => el.textContent === "Lot");
    expect(dt?.nextElementSibling?.textContent).toContain("2");
    expect(dt?.nextElementSibling?.textContent).toContain("Fibra");
    first.unmount();
    get.mockResolvedValueOnce(workspaceFixture());
    const second = render(<WorkspacePage />);
    await flush();
    expect([...second.container.querySelectorAll("dt")].map((el) => el.textContent)).not.toContain("Lot");
  });

  it("termini: dies naturals (regla única de lib/deadline, A11) i data quan hi és; «no consta» quan és null", async () => {
    // El pilot: a 11 dies i 18 hores, aquí es deia «11» i a la fitxa «12». Ara 12 a tot arreu.
    vi.setSystemTime(new Date("2026-09-24T16:00:00Z"));
    get.mockResolvedValueOnce(workspaceFixture());
    const first = render(<WorkspacePage />);
    await flush();
    expect(first.container.textContent).toContain("12 dies");
    expect(first.container.textContent).not.toContain("11 dies");
    vi.useRealTimers();
    first.unmount();
    get.mockResolvedValueOnce(workspaceFixture({ deadline_at: null, days_remaining: null }));
    const second = render(<WorkspacePage />);
    await flush();
    expect(second.container.textContent).toContain("Termini no consta");
  });

  it("404: la candidatura ja no hi és, amb sortida a Candidatures", async () => {
    get.mockRejectedValue(new ApiError(404, "not_found", "Not found"));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("Aquesta candidatura ja no hi és")).toBeTruthy();
    expect((screen.getByRole("link", { name: /Ves a Candidatures/ }) as HTMLAnchorElement).getAttribute("href")).toBe("/candidatures");
  });

  it("un altre error es pot reintentar", async () => {
    get.mockRejectedValueOnce(new ApiError(500, "internal_error", "Error intern"));
    get.mockResolvedValueOnce(workspaceFixture());
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("No s'ha pogut carregar la candidatura")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Torna-ho a provar/ }));
    await flush();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
  });

  it("una recomanació GO sense decisió humana no mou el pas", async () => {
    get.mockResolvedValue(workspaceFixture({ recommendation: "go_candidate", eligibility: "eligible" }));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
    // Només la fixació inicial del pas real (Decidir); cap salt a Preparar.
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/candidatures/w1?pas=decidir", { scroll: false });
    expect(push).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  async function decide(name: RegExp) {
    fireEvent.click(screen.getByRole("button", { name }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click([...dialog.querySelectorAll("button")].find((b) => /^(Signa|Registra)/.test(b.textContent ?? ""))!);
    await flush();
  }

  it("GO humà desat: POST, refresc sense desmuntar i després ?pas=preparar", async () => {
    get.mockResolvedValueOnce(workspaceFixture());
    post.mockResolvedValue({});
    const decided = workspaceFixture({ status: "go_approved", human_decision: "go", latest_decision: decisionFixture(), decisions: [decisionFixture()] });
    get.mockResolvedValueOnce(decided);
    const { container } = render(<WorkspacePage />);
    await flush();
    replace.mockClear();
    await decide(/Presentar-se · GO/);
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(2);
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    // El progrés surt de l'estat real que ha tornat el backend...
    expect(step("Decidir").closest("[data-state]")?.getAttribute("data-state")).toBe("done");
    expect(step("Preparar").closest("[data-state]")?.getAttribute("data-state")).toBe("on");
    // ...i el pas que es mira passa a Preparar, sense scroll i sense push.
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/candidatures/w1?pas=preparar", { scroll: false });
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByTestId("dossier")).toBeTruthy();
    expect(screen.queryByText("Ens presentem a aquesta licitació?")).toBeNull();
  });

  it("GO fallit: cap refresc i es continua a Decidir", async () => {
    get.mockResolvedValue(workspaceFixture());
    post.mockRejectedValue(new ApiError(500, "internal_error", "Error intern"));
    render(<WorkspacePage />);
    await flush();
    replace.mockClear();
    await decide(/Presentar-se · GO/);
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(1);
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog").textContent).toContain("No s'ha pogut registrar la decisió");
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
  });

  it("NO-GO desat: refresc, però no es va a Preparar", async () => {
    get.mockResolvedValueOnce(workspaceFixture());
    post.mockResolvedValue({});
    const noGo = decisionFixture({ decision: "no_go", reason: null });
    get.mockResolvedValueOnce(workspaceFixture({ status: "no_go_approved", human_decision: "no_go", latest_decision: noGo, decisions: [noGo] }));
    render(<WorkspacePage />);
    await flush();
    replace.mockClear();
    await decide(/No presentar-se · NO-GO/);
    expect(get).toHaveBeenCalledTimes(2);
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
    expect(screen.queryByTestId("dossier")).toBeNull();
    expect(step("Preparar").closest("[data-state]")?.getAttribute("data-state")).toBe("todo");
  });

  it("Ajornar desat: refresc, però no es va a Preparar", async () => {
    get.mockResolvedValueOnce(workspaceFixture());
    post.mockResolvedValue({});
    const deferred = decisionFixture({ decision: "defer", reason: null });
    get.mockResolvedValueOnce(workspaceFixture({ status: "awaiting_decision", human_decision: "defer", latest_decision: deferred, decisions: [deferred] }));
    render(<WorkspacePage />);
    await flush();
    replace.mockClear();
    await decide(/^Ajornar/);
    expect(get).toHaveBeenCalledTimes(2);
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
    expect(step("Decidir").closest("[data-state]")?.getAttribute("data-state")).toBe("on");
  });

  it("decision_reason_required: un sol GET extra del workspace i un de l'anàlisi, cap navegació i el modal continua obert", async () => {
    get.mockResolvedValueOnce(workspaceFixture({ go_requires_reason: false, decision_warnings: [] }));
    get.mockResolvedValueOnce(workspaceFixture({ go_requires_reason: true, decision_warnings: ["analysis_stale"] }));
    post.mockRejectedValue(
      new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_stale"], basis: "analysis_v3" }),
    );
    const { container } = render(<WorkspacePage />);
    await flush();
    expect(get).toHaveBeenCalledTimes(1);
    expect(analysisCurrent).toHaveBeenCalledTimes(1);
    expect(readiness).toHaveBeenCalledTimes(1);
    replace.mockClear();
    await decide(/Presentar-se · GO/);
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(2);
    expect(get.mock.calls[1][0]).toMatch(/\/companies\/c1\/workspaces\/w1$/);
    expect(analysisCurrent).toHaveBeenCalledTimes(2);
    expect(readiness).toHaveBeenCalledTimes(1);
    expect(replace).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Cal un motiu: ");
    expect(dialog.textContent).toContain("Motiu (obligatori: hi ha avisos de l'anàlisi del plec)");
  });

  describe("conflicte decision_reason_required: cursa amb l'anàlisi", () => {
    async function conflict(second: "stale" | "404") {
      get.mockResolvedValueOnce(workspaceFixture({ go_requires_reason: false, decision_warnings: [] }));
      analysisCurrent.mockReset();
      analysisCurrent.mockResolvedValueOnce(analysisFixture({ id: "run-1" }));
      if (second === "stale") analysisCurrent.mockResolvedValueOnce(analysisFixture({ id: "run-1", stale: true }));
      else analysisCurrent.mockRejectedValueOnce(new ApiError(404, "not_found", "Not found"));
      get.mockResolvedValueOnce(workspaceFixture({ go_requires_reason: true, decision_warnings: ["analysis_stale"] }));
      post.mockRejectedValue(new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_stale"], basis: "analysis_v3" }));
      render(<WorkspacePage />);
      await flush();
      expect(get).toHaveBeenCalledTimes(1);
      expect(analysisCurrent).toHaveBeenCalledTimes(1);
      replace.mockClear();
      await decide(/Presentar-se · GO/);
      await flush();
      expect(post).toHaveBeenCalledTimes(1);
      expect(get).toHaveBeenCalledTimes(2);
      expect(analysisCurrent).toHaveBeenCalledTimes(2);
      expect(replace).not.toHaveBeenCalled();
      expect(screen.getByText("Ens presentem a aquesta licitació?")).toBeTruthy();
      return screen.getByRole("dialog");
    }

    it("anàlisi vigent que passa a desactualitzada: la fila del modal ho diu i el panell passa a mode obligatori", async () => {
      const dialog = await conflict("stale");
      expect(dialog.textContent).toContain("desactualitzada");
      expect(dialog.textContent).toContain("Motiu (obligatori: hi ha avisos de l'anàlisi del plec)");
      expect(screen.getByTestId("panel-spy").getAttribute("data-kind")).toBe("stale");
      expect(screen.getByTestId("card-spy").getAttribute("data-kind")).toBe("stale");
    });

    it("l'anàlisi desapareix (404): la fila diu que no n'hi ha", async () => {
      const dialog = await conflict("404");
      expect(dialog.textContent).toContain("Encara no hi ha anàlisi d'aquesta licitació.");
      expect(screen.getByTestId("panel-spy").getAttribute("data-kind")).toBe("absent");
    });

    it("camí normal: obrir i tancar el modal sense conflicte no fa cap GET addicional", async () => {
      get.mockResolvedValue(workspaceFixture());
      analysisCurrent.mockReset();
      analysisCurrent.mockResolvedValue(analysisFixture());
      render(<WorkspacePage />);
      await flush();
      fireEvent.click(screen.getByRole("button", { name: /Presentar-se · GO/ }));
      fireEvent.click(screen.getByRole("button", { name: /Cancel·la/ }));
      await flush();
      expect(get).toHaveBeenCalledTimes(1);
      expect(analysisCurrent).toHaveBeenCalledTimes(1);
    });
  });

  it("advance acceptat: es rellegeix el workspace i, si ja és a revisió final, s'obre Tancar", async () => {
    search = "pas=preparar";
    const go = decisionFixture();
    get.mockResolvedValueOnce(workspaceFixture({ status: "preparing_bid", human_decision: "go", latest_decision: go, decisions: [go] }));
    get.mockResolvedValueOnce(workspaceFixture({ status: "ready_for_final_review", human_decision: "go", latest_decision: go, decisions: [go], readiness: "ready_for_final_review", readiness_blockers: [] }));
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "simula advance acceptat" }));
    await flush();
    expect(get).toHaveBeenCalledTimes(2);
    expect(step("Tancar").closest("[data-state]")?.getAttribute("data-state")).toBe("on");
    expect(step("Preparar").closest("[data-state]")?.getAttribute("data-state")).toBe("done");
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/candidatures/w1?pas=tancar", { scroll: false });
    expect(push).not.toHaveBeenCalled();
  });

  it("advance «acceptat» però l'estat real no ha canviat: cap navegació", async () => {
    search = "pas=preparar";
    get.mockResolvedValue(workspaceFixture({ status: "preparing_bid", human_decision: "go", latest_decision: decisionFixture(), decisions: [decisionFixture()] }));
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "simula advance acceptat" }));
    await flush();
    expect(get).toHaveBeenCalledTimes(2);
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByTestId("dossier")).toBeTruthy();
  });
});

describe("Candidatura · Preparar per àrees", () => {
  const go = () => ({ status: "preparing_bid" as const, human_decision: "go" as const, latest_decision: decisionFixture(), decisions: [decisionFixture()] });

  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    search = "pas=preparar";
    leaveGuard.mockReset();
    leaveGuard.mockResolvedValue(true);
    get.mockResolvedValue(workspaceFixture(go()));
  });

  const areas = () => screen.getAllByRole("tab").map((tab) => tab.textContent);

  it("quatre àrees, i cap «visió general»", async () => {
    render(<WorkspacePage />);
    await flush();
    expect(areas()).toEqual(["Solució tècnica", "Oferta econòmica", "Dossier", "Pla de treball"]);
    expect(areas()).not.toContain("Visió general");
  });

  it("sense àrea a la URL, Preparar obre el dossier: cap enllaç antic no canvia de pantalla", async () => {
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("dossier")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Dossier" }).getAttribute("aria-selected")).toBe("true");
    // Fixar el pas no ha d'afegir cap àrea a la URL.
    expect(replace).not.toHaveBeenCalledWith(expect.stringContaining("area="), expect.anything());
  });

  it("una àrea desconeguda cau al dossier en comptes de deixar la pantalla buida", async () => {
    search = "pas=preparar&area=visio-general";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("dossier")).toBeTruthy();
  });

  it("un enllaç directe obre l'àrea que demana", async () => {
    search = "pas=preparar&area=oferta";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByRole("tab", { name: "Oferta econòmica" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("dossier")).toBeNull();
    expect(screen.queryByTestId("checklist")).toBeNull();
  });

  it("canviar d'àrea escriu `area` a la URL, conserva el pas i deixa tornar enrere", async () => {
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("tab", { name: "Solució tècnica" }));
    // Abans de navegar, la pestanya pregunta a l'àrea si es pot marxar.
    await flush();
    // `push`, no `replace`: Enrere ha de desfer el canvi d'àrea, igual que
    // desfà un enllaç del calaix de preparació.
    expect(push).toHaveBeenCalledWith("/candidatures/w1?pas=preparar&area=solucio", { scroll: false });
    expect(replace).not.toHaveBeenCalled();
  });

  it("l'àrea la mana la URL: Enrere i Endavant tornen a pintar el que toca", async () => {
    search = "pas=preparar&area=oferta";
    const primer = render(<WorkspacePage />);
    await flush();
    expect(screen.getByRole("tab", { name: "Oferta econòmica" }).getAttribute("aria-selected")).toBe("true");
    primer.unmount();
    // El navegador torna a l'entrada anterior: no hi ha cap estat local que
    // contradigui la URL, així que la pantalla la segueix.
    search = "pas=preparar&area=pla";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByRole("tab", { name: "Pla de treball" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("checklist")).toBeTruthy();
  });

  it("el dossier només surt a la seva àrea", async () => {
    search = "pas=preparar&area=pla";
    render(<WorkspacePage />);
    await flush();
    expect(screen.queryByTestId("dossier")).toBeNull();
  });

  it("el pla de treball reuneix checklist, tasques i notes", async () => {
    search = "pas=preparar&area=pla";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("checklist")).toBeTruthy();
    expect(screen.getByTestId("tasks")).toBeTruthy();
    expect(screen.getByTestId("notes")).toBeTruthy();
  });

  it("area=oferta obre l'oferta econòmica, i ja no queda cap àrea buida", async () => {
    search = "pas=preparar&area=oferta";
    const { container } = render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("oferta")).toBeTruthy();
    expect(container.textContent).not.toContain("Encara no està disponible");
    expect(screen.queryByTestId("dossier")).toBeNull();
    expect(screen.queryByTestId("solucio")).toBeNull();
  });

  it("area=solucio obre la solució tècnica", async () => {
    search = "pas=preparar&area=solucio";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("solucio")).toBeTruthy();
    expect(screen.queryByTestId("dossier")).toBeNull();
    expect(screen.queryByText("Encara no està disponible")).toBeNull();
  });

  it("les àrees són de Preparar: als altres passos no hi surten", async () => {
    search = "pas=decidir";
    render(<WorkspacePage />);
    await flush();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("la preparació final es llegeix un cop i es veu al capçal; el pas Tancar diu el mateix nombre (A15)", async () => {
    readiness.mockResolvedValue(readinessFixture({ findings: [{ code: "economic_lot_empty", severity: "blocker", domain: "economic", entity_id: null, context: {} }] }));
    // Dos blocadors al workspace, un a la preparació final: només compta la preparació final.
    get.mockResolvedValueOnce(workspaceFixture({ readiness_blockers: ["missing_documents", "open_tasks"] }));
    render(<WorkspacePage />);
    await flush();
    expect(readiness).toHaveBeenCalledTimes(1);
    expect(readiness).toHaveBeenCalledWith("/companies/c1/workspaces/w1/final-readiness");
    expect(screen.getAllByText(/1 pendent de preparació/).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/2 (coses|pendents)/)).toBeNull();
  });

  it("«Veure la licitació» és a la capçalera, fora dels badges, a Decidir, Preparar i Tancar (A16)", async () => {
    for (const pas of ["decidir", "preparar", "tancar"]) {
      search = `pas=${pas}`;
      get.mockResolvedValueOnce(workspaceFixture());
      const view = render(<WorkspacePage />);
      await flush();
      const link = screen.getByRole("link", { name: "Veure la licitació" });
      expect(link.getAttribute("href")).toBe("/licitacions/t1");
      // Separat dels badges: no és dins del mateix contenidor que la pastilla d'estat.
      const pill = screen.getByText("Pendent de decisió");
      expect(pill.parentElement?.contains(link)).toBe(false);
      view.unmount();
    }
  });

  it("l'estat «analysis» del capçal s'etiqueta segons `analysis_state` del dossier (A13)", async () => {
    get.mockResolvedValueOnce(
      workspaceFixture({
        status: "analysis",
        dossier_coverage: { analysis_state: "absent", mandatory_total: null, covered: 0, missing_document: 0, needs_review: 0, not_applicable: 0, expired_documents: 0, optional_total: 0, optional_covered: 0 },
      }),
    );
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("Pendent d'anàlisi")).toBeTruthy();
    expect(screen.queryByText("En anàlisi")).toBeNull();
  });

  it("el comptador del capçal diu que són totes les àrees, i cada pestanya de Preparar porta el seu (B6)", async () => {
    const blocker = (code: string, domain: "dossier" | "economic") => ({ code, severity: "blocker" as const, domain, entity_id: null, context: {} });
    readiness.mockResolvedValue(
      readinessFixture({
        findings: [
          blocker("mandatory_unresolved", "dossier"),
          blocker("mandatory_evidence_missing", "dossier"),
          blocker("economic_lot_empty", "economic"),
          blocker("mandatory_checklist_blocked", "dossier"),
        ],
      }),
    );
    search = "pas=preparar&area=dossier";
    get.mockResolvedValueOnce(workspaceFixture({ status: "preparing_bid", human_decision: "go" }));
    render(<WorkspacePage />);
    await flush();
    // «4» al capçal és de totes les àrees; «2» a Dossier, i les altres pestanyes sumen la resta.
    expect(screen.getByText("4 pendents de preparació (totes les àrees)")).toBeTruthy();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Solució tècnica",
      "Oferta econòmica 1 pendent",
      "Dossier 2 pendents",
      "Pla de treball 1 pendent",
    ]);
    // P2-02: el nom accessible diu què compta el número, no només «Dossier 2».
    expect(screen.getByRole("tab", { name: "Dossier 2 pendents" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Oferta econòmica 1 pendent" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Solució tècnica" })).toBeTruthy();
  });

  it("un desat del dossier torna a preguntar la preparació final i el capçal canvia", async () => {
    const blocker = { code: "mandatory_evidence_missing", severity: "blocker" as const, domain: "dossier" as const, entity_id: null, context: {} };
    readiness.mockResolvedValueOnce(readinessFixture({ findings: [blocker] }));
    readiness.mockResolvedValueOnce(readinessFixture());
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "simula desat del dossier" }));
    await flush();

    expect(readiness).toHaveBeenCalledTimes(2);
    expect(screen.getByText("A punt")).toBeTruthy();
    expect(screen.queryByText("1 pendent de preparació (totes les àrees)")).toBeNull();
    // Sense recarregar la pàgina: la secció no s'ha desmuntat.
    expect(screen.getByTestId("dossier")).toBeTruthy();
  });

  it("sense mutacions no es torna a preguntar res: no hi ha cap sondeig", async () => {
    render(<WorkspacePage />);
    await flush();
    await flush();
    expect(readiness).toHaveBeenCalledTimes(1);
  });

  it("si la preparació falla, la candidatura es continua podent fer servir", async () => {
    readiness.mockRejectedValue(Object.assign(new Error("Error intern"), { status: 500 }));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("Preparació desconeguda")).toBeTruthy();
    expect(screen.queryByText("A punt")).toBeNull();
    expect(screen.getByTestId("dossier")).toBeTruthy();
  });
});

describe("Candidatura · canvis sense desar i els tres passos", () => {
  const go = () => ({ status: "preparing_bid" as const, human_decision: "go" as const, latest_decision: decisionFixture(), decisions: [decisionFixture()] });

  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    search = "pas=preparar&area=solucio";
    leaveGuard.mockReset();
    leaveGuard.mockResolvedValue(true);
    get.mockResolvedValue(workspaceFixture(go()));
  });

  const stepLink = (name: string) => screen.getByRole("link", { name: new RegExp(`^${name}`) });

  async function makeDirty() {
    fireEvent.click(screen.getByRole("button", { name: "simula canvis sense desar" }));
    await flush();
  }

  it("amb canvis sense desar, anar a Tancar pregunta abans de res", async () => {
    render(<WorkspacePage />);
    await flush();
    await makeDirty();
    leaveGuard.mockResolvedValue(false);

    await act(async () => {
      fireEvent.click(stepLink("Tancar"));
    });
    expect(leaveGuard).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
  });

  it("si es cancel·la, no es mou de la solució tècnica", async () => {
    render(<WorkspacePage />);
    await flush();
    await makeDirty();
    leaveGuard.mockResolvedValue(false);

    await act(async () => {
      fireEvent.click(stepLink("Decidir"));
    });
    await flush();
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByTestId("solucio")).toBeTruthy();
    expect(stepLink("Preparar").getAttribute("aria-current")).toBe("step");
  });

  it("si es confirma, es navega", async () => {
    render(<WorkspacePage />);
    await flush();
    await makeDirty();
    leaveGuard.mockResolvedValue(true);

    await act(async () => {
      fireEvent.click(stepLink("Tancar"));
    });
    await flush();
    expect(push).toHaveBeenCalledWith("/candidatures/w1?pas=tancar&area=solucio");
  });

  it("sense canvis pendents no pregunta res i l'enllaç segueix sent el de sempre", async () => {
    render(<WorkspacePage />);
    await flush();
    await makeDirty();
    fireEvent.click(screen.getByRole("button", { name: "simula desat" }));
    await flush();

    await act(async () => {
      fireEvent.click(stepLink("Tancar"));
    });
    expect(leaveGuard).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(stepLink("Tancar").getAttribute("href")).toBe("/candidatures/w1?pas=tancar&area=solucio");
  });

  it("les pestanyes d'àrea fan la mateixa pregunta", async () => {
    render(<WorkspacePage />);
    await flush();
    await makeDirty();
    leaveGuard.mockResolvedValue(false);

    fireEvent.click(screen.getByRole("tab", { name: "Dossier" }));
    await flush();
    expect(leaveGuard).toHaveBeenCalledTimes(1);
    expect(push).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("Candidatura · integració final d'Sprint 2", () => {
  const go = () => ({ status: "preparing_bid" as const, human_decision: "go" as const, latest_decision: decisionFixture(), decisions: [decisionFixture()] });

  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    leaveGuard.mockReset();
    leaveGuard.mockResolvedValue(true);
    economicGuard.mockReset();
    economicGuard.mockResolvedValue(true);
    search = "pas=preparar&area=solucio";
    get.mockResolvedValue(workspaceFixture(go()));
  });

  const stepLink = (name: string) => screen.getByRole("link", { name: new RegExp(`^${name}`) });

  it("la guarda d'una àrea no sobreviu quan se'n va", async () => {
    const view = render(<WorkspacePage />);
    await flush();
    // La solució tècnica registra que té feina sense desar...
    fireEvent.click(screen.getByRole("button", { name: "simula canvis sense desar" }));
    await flush();
    expect(leaveGuard).not.toHaveBeenCalled();

    // ...i se'n va a l'oferta, que no en registra cap.
    search = "pas=preparar&area=oferta";
    view.rerender(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("oferta")).toBeTruthy();
    expect(screen.queryByTestId("solucio")).toBeNull();

    // Ara anar a Tancar no ha de preguntar res: la guarda anterior ja no hi és.
    await act(async () => {
      fireEvent.click(stepLink("Tancar"));
    });
    await flush();
    expect(leaveGuard).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cada àrea fa la seva pregunta, i només una", async () => {
    search = "pas=preparar&area=oferta";
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "oferta amb canvis" }));
    await flush();
    economicGuard.mockResolvedValue(false);

    await act(async () => {
      fireEvent.click(stepLink("Decidir"));
    });
    await flush();
    expect(economicGuard).toHaveBeenCalledTimes(1);
    expect(leaveGuard).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("després de desar, la navegació deixa de preguntar", async () => {
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "simula canvis sense desar" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "simula desat" }));
    await flush();

    await act(async () => {
      fireEvent.click(stepLink("Tancar"));
    });
    expect(leaveGuard).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it("Preparar continua editable amb una revisió congelada i aprovada (C-12)", async () => {
    get.mockResolvedValue(workspaceFixture({ ...go(), status: "approved_for_submission" }));
    render(<WorkspacePage />);
    await flush();
    // L'àrea continua sent la de sempre, sense cap mode de només lectura.
    expect(screen.getByTestId("solucio")).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(document.body.textContent).not.toMatch(/bloquejada|només lectura|no es pot editar/i);
  });

  it("una revisió obsoleta tampoc no tanca Preparar", async () => {
    get.mockResolvedValue(workspaceFixture({ ...go(), status: "ready_for_final_review" }));
    search = "pas=preparar&area=oferta";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("oferta")).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(4);
  });

  it("els tres passos continuen sent enllaços de debò", async () => {
    render(<WorkspacePage />);
    await flush();
    for (const step of ["Decidir", "Preparar", "Tancar"]) {
      const link = stepLink(step) as HTMLAnchorElement;
      expect(link.tagName).toBe("A");
      expect(link.getAttribute("href")).toContain("?pas=");
    }
    // Amb modificador, el clic no s'intercepta: obrir en una pestanya nova
    // ha de continuar funcionant.
    fireEvent.click(screen.getByRole("button", { name: "simula canvis sense desar" }));
    await flush();
    await act(async () => {
      fireEvent.click(stepLink("Tancar"), { metaKey: true });
    });
    expect(leaveGuard).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });
});

describe("Candidatura · propietari únic de /analysis/current", () => {
  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    analysisCurrent.mockReset();
    analysisCurrent.mockResolvedValue(analysisFixture({ id: "run-xyz" }));
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    get.mockResolvedValue(workspaceFixture());
  });

  it("Decidir: una sola lectura i el mateix run arriba a la targeta i al panell", async () => {
    search = "";
    render(<WorkspacePage />);
    await flush();
    expect(analysisCurrent).toHaveBeenCalledTimes(1);
    expect(analysisCurrent.mock.calls[0][0]).toContain("/analysis/current");
    expect(screen.getByTestId("card-spy").getAttribute("data-run-id")).toBe("run-xyz");
    expect(screen.getByTestId("panel-spy").getAttribute("data-run-id")).toBe("run-xyz");
    expect(screen.getByTestId("card-spy").getAttribute("data-kind")).toBe("current");
    expect(screen.getByTestId("panel-spy").getAttribute("data-kind")).toBe("current");
    expect(screen.queryByText(/run-xyz/)).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("obrir i tancar el modal no torna a llegir l'anàlisi", async () => {
    search = "";
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /Presentar-se · GO/ }));
    await flush();
    fireEvent.change(screen.getAllByRole("textbox")[0], { target: { value: "Motiu de prova" } });
    fireEvent.click(screen.getByRole("button", { name: /Cancel·la/ }));
    await flush();
    expect(analysisCurrent).toHaveBeenCalledTimes(1);
  });

  it.each(["preparar", "tancar"])("%s: cap lectura de /analysis/current", async (pas) => {
    search = `pas=${pas}`;
    render(<WorkspacePage />);
    await flush();
    expect(screen.queryByTestId("card-spy")).toBeNull();
    expect(analysisCurrent).not.toHaveBeenCalled();
  });
});

describe("Candidatura · Preparar sense GO és visible però només lectura (3B-2 §7.1.6, A18)", () => {
  const COPY = "Es pot consultar; s'obre a edició amb GO";
  // Ajust del 22-10-2026: notes i dubtes s'escriuen en l'anàlisi; només un NO-GO els tanca.
  const STUDY = "Les notes i els dubtes ja s'hi poden escriure";
  const go = () => ({ status: "preparing_bid" as const, human_decision: "go" as const, latest_decision: decisionFixture(), decisions: [decisionFixture()] });
  const noGo = () => ({ status: "no_go_approved" as const, human_decision: "no_go" as const, latest_decision: decisionFixture({ decision: "no_go" }) });

  beforeEach(() => {
    get.mockReset();
    readiness.mockReset();
    readiness.mockResolvedValue(readinessFixture());
    analysisCurrent.mockReset();
    analysisCurrent.mockRejectedValue(new ApiError(404, "not_found", "Not found"));
    post.mockReset();
    push.mockClear();
    replace.mockClear();
    leaveGuard.mockReset();
    leaveGuard.mockResolvedValue(true);
    search = "pas=preparar&area=solucio";
  });

  function readOnlyArea(): HTMLFieldSetElement {
    const area = document.querySelector("fieldset[data-prepare-mode='read-only']") as HTMLFieldSetElement | null;
    expect(area, "l'àrea de Preparar en mode lectura és un fieldset desactivat").not.toBeNull();
    return area!;
  }

  it("sense cap decisió, ?pas=preparar es manté navegable: l'àrea es veu, els controls queden desactivats i la copy ho diu", async () => {
    get.mockResolvedValue(workspaceFixture());
    render(<WorkspacePage />);
    await flush();
    // Es veu: el pas, les quatre àrees i la secció demanada.
    expect(step("Preparar").getAttribute("aria-current")).toBe("step");
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByTestId("solucio")).toBeTruthy();
    // Però no s'edita: tot control de l'àrea penja d'un fieldset desactivat.
    const area = readOnlyArea();
    expect(area.disabled).toBe(true);
    const control = screen.getByRole("button", { name: "simula canvis sense desar" });
    expect(control.closest("fieldset[disabled]")).not.toBeNull();
    expect(screen.getByText(COPY)).toBeTruthy();
    // En l'anàlisi les notes i els dubtes ja s'escriuen, i la nota de lectura ho diu.
    expect(screen.getByText(STUDY)).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("amb NO-GO vigent, Preparar també és només lectura", async () => {
    get.mockResolvedValue(workspaceFixture(noGo()));
    render(<WorkspacePage />);
    await flush();
    expect(readOnlyArea().disabled).toBe(true);
    expect(screen.getByText(COPY)).toBeTruthy();
    expect(screen.queryByText(STUDY)).toBeNull();
  });

  it("un ajornament tampoc no obre l'edició de Preparar, però sí les notes i els dubtes", async () => {
    get.mockResolvedValue(workspaceFixture({ human_decision: "defer", latest_decision: decisionFixture({ decision: "defer" }) }));
    render(<WorkspacePage />);
    await flush();
    expect(readOnlyArea().disabled).toBe(true);
    expect(screen.getByText(STUDY)).toBeTruthy();
  });

  it("amb GO vigent és editable: cap fieldset desactivat i cap copy de lectura", async () => {
    get.mockResolvedValue(workspaceFixture(go()));
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("solucio")).toBeTruthy();
    expect(document.querySelector("fieldset[data-prepare-mode='read-only']")).toBeNull();
    expect(document.querySelector("fieldset[disabled]")).toBeNull();
    expect(screen.queryByText(COPY)).toBeNull();
    expect(screen.getByRole("button", { name: "simula canvis sense desar" }).closest("fieldset[disabled]")).toBeNull();
  });

  it("cada àrea de Preparar queda en lectura sense GO, i la copy porta a Decidir", async () => {
    get.mockResolvedValue(workspaceFixture());
    for (const [area, testId] of [["dossier", "dossier"], ["oferta", "oferta"], ["pla", "tasks"]] as const) {
      search = `pas=preparar&area=${area}`;
      const { unmount } = render(<WorkspacePage />);
      await flush();
      expect(screen.getByTestId(testId)).toBeTruthy();
      expect(screen.getByTestId(testId).closest("fieldset[disabled]"), area).not.toBeNull();
      expect(screen.getByRole("link", { name: "Ves a Decidir" }).getAttribute("href")).toContain("pas=decidir");
      unmount();
    }
  });

  it("en l'anàlisi, al pla de treball les notes ja s'editen; checklist i tasques, no", async () => {
    get.mockResolvedValue(workspaceFixture());
    search = "pas=preparar&area=pla";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("tasks").closest("fieldset[disabled]")).not.toBeNull();
    expect(screen.getByTestId("checklist").closest("fieldset[disabled]")).not.toBeNull();
    expect(screen.getByTestId("notes").closest("fieldset[disabled]")).toBeNull();
    expect(screen.getByTestId("notes").closest("fieldset[data-study-mode='edit']")).not.toBeNull();
  });

  it("amb NO-GO vigent, les notes tornen a ser només lectura", async () => {
    get.mockResolvedValue(workspaceFixture(noGo()));
    search = "pas=preparar&area=pla";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("notes").closest("fieldset[disabled]")).not.toBeNull();
    expect(screen.getByTestId("notes").closest("fieldset[data-study-mode='read-only']")).not.toBeNull();
  });

  it("en l'anàlisi, el calaix de dubtes ja s'edita sense GO: cap fieldset desactivat ni copy de lectura", async () => {
    get.mockImplementation(async (path: string) => (path.endsWith("/issues") ? [] : workspaceFixture()));
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Dubtes i discrepàncies" }));
    await flush();
    const drawer = screen.getByRole("dialog", { name: "Dubtes i discrepàncies" });
    expect(drawer.textContent).toContain("Cap dubte obert");
    expect(drawer.textContent).not.toContain(COPY);
    expect(drawer.querySelector("fieldset[disabled]")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("amb NO-GO vigent, els dubtes es poden consultar però no obrir-ne cap", async () => {
    get.mockImplementation(async (path: string) => (path.endsWith("/issues") ? [] : workspaceFixture(noGo())));
    search = "pas=preparar";
    render(<WorkspacePage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Dubtes i discrepàncies" }));
    await flush();
    const drawer = screen.getByRole("dialog", { name: "Dubtes i discrepàncies" });
    expect(drawer.textContent).toContain("Cap dubte obert");
    expect(drawer.textContent).toContain(COPY);
    // Tot el cos del calaix (llista i formularis) penja d'un fieldset desactivat.
    const body = drawer.querySelector("fieldset[disabled]");
    expect(body).not.toBeNull();
    expect(body?.textContent).toContain("Cap dubte obert");
    // Tancar el calaix continua sent possible: el botó de tancar no és dins del fieldset.
    expect(screen.getByRole("button", { name: "Tanca" }).closest("fieldset[disabled]")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("Tancar sense GO també és només lectura: el paquet no es genera des de la UI", async () => {
    get.mockResolvedValue(workspaceFixture());
    search = "pas=tancar";
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByTestId("package").closest("fieldset[disabled]")).not.toBeNull();
    expect(screen.getByText(COPY)).toBeTruthy();
  });
});
