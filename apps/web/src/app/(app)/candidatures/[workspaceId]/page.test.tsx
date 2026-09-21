import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readinessFixture } from "@/test/readiness-fixture";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

const get = vi.fn();
const readiness = vi.fn();
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
    typeof path === "string" && path.includes("/final-readiness") ? readiness(path, ...rest) : get(path, ...rest);
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
vi.mock("@/components/WorkspaceWork", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/WorkspaceWork")>();
  return {
    ...actual,
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
        analysis_outdated: true,
        outdated_reasons: ["requirements_outdated"],
        open_warnings: ["deadline_passed", "outdated_analysis"],
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
    expect(banners[0].textContent).toContain("Els plecs han canviat");
  });

  it("anàlisi endarrerida: es diu, la decisió vigent es continua veient i no es recalcula res", async () => {
    const decision = decisionFixture();
    get.mockResolvedValue(
      workspaceFixture({
        status: "go_approved",
        human_decision: "go",
        latest_decision: decision,
        decisions: [decision],
        analysis_outdated: true,
        outdated_reasons: ["compliance_run_changed"],
        open_warnings: ["outdated_analysis"],
      }),
    );
    search = "pas=decidir";
    const { container } = render(<WorkspacePage />);
    await flush();
    const banner = container.querySelector("[role='status'][data-level]")!;
    expect(banner.getAttribute("data-level")).toBe("attention");
    expect(banner.textContent).toContain("Anàlisi");
    expect(container.textContent).toContain("Decisió vigent");
    expect(container.textContent).toContain("Anna Puig");
    expect(post).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("termini: dies i data quan hi és; «no consta» quan és null", async () => {
    get.mockResolvedValueOnce(workspaceFixture());
    const first = render(<WorkspacePage />);
    await flush();
    expect(first.container.textContent).toContain("21 dies restants");
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

  it("les àrees que encara no existeixen ho diuen, sense ensenyar dades inventades", async () => {
    search = "pas=preparar&area=solucio";
    const { container } = render(<WorkspacePage />);
    await flush();
    expect(container.textContent).toContain("Encara no està disponible");
    expect(container.querySelector("table")).toBeNull();
    expect(screen.queryByTestId("dossier")).toBeNull();
  });

  it("les àrees són de Preparar: als altres passos no hi surten", async () => {
    search = "pas=decidir";
    render(<WorkspacePage />);
    await flush();
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("la preparació final es llegeix un cop i es veu al capçal", async () => {
    readiness.mockResolvedValue(readinessFixture({ findings: [{ code: "economic_lot_empty", severity: "blocker", domain: "economic", entity_id: null, context: {} }] }));
    render(<WorkspacePage />);
    await flush();
    expect(readiness).toHaveBeenCalledTimes(1);
    expect(readiness).toHaveBeenCalledWith("/companies/c1/workspaces/w1/final-readiness");
    expect(screen.getByText("1 bloqueig")).toBeTruthy();
  });

  it("un desat del dossier torna a preguntar la preparació final i el capçal canvia", async () => {
    const blocker = { code: "mandatory_evidence_missing", severity: "blocker" as const, domain: "dossier" as const, entity_id: null, context: {} };
    readiness.mockResolvedValueOnce(readinessFixture({ findings: [blocker] }));
    readiness.mockResolvedValueOnce(readinessFixture());
    render(<WorkspacePage />);
    await flush();
    expect(screen.getByText("1 bloqueig")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "simula desat del dossier" }));
    await flush();

    expect(readiness).toHaveBeenCalledTimes(2);
    expect(screen.getByText("A punt")).toBeTruthy();
    expect(screen.queryByText("1 bloqueig")).toBeNull();
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
