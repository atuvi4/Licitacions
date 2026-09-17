import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

const get = vi.fn();
const post = vi.fn();
const push = vi.fn();
const replace = vi.fn();
let search = "";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) } };
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
vi.mock("@/components/WorkspaceDossier", () => ({ DossierSection: () => <div data-testid="dossier">Dossier (secció actual)</div> }));
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
    expect(screen.getByTestId("checklist")).toBeTruthy();
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
});
