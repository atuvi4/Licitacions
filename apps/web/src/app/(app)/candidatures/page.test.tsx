import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkspaceSummary } from "@/lib/workspace";

const get = vi.fn();
const push = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1" }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/candidatures",
}));

import WorkspacesPage from "./page";

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void };

function workspace(overrides: Partial<WorkspaceSummary>): WorkspaceSummary {
  return {
    id: "w",
    company_id: "c1",
    tender_id: "t",
    tender_lot_id: null,
    status: "awaiting_decision",
    tender_title: "Licitació",
    expediente_number: "EXP-1",
    buyer_name: "Ajuntament Demo",
    deadline_at: null,
    days_remaining: null,
    deadline_passed: false,
    prefit_score: 88,
    eligibility: "eligible",
    recommendation: "go_candidate",
    human_decision: null,
    analysis_basis: "real",
    analysis_outdated: false,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

const summary = { total_tenders: 148, actionable: 100, candidates: 12, high_fit: 5, low_fit: 3, excluded: 1, closed: 2, insufficient_data: 1, stale: 0, rules_version: "v1", last_calculated_at: null };
const HEADERS = ["Licitació", "Termini", "Encaix", "Resultat de l'anàlisi", "Decisió", "Estat", "Acció"];

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

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Candidatures (llistat)", () => {
  beforeEach(() => {
    get.mockReset();
    push.mockReset();
    // El comptador de dies es calcula al client sobre `deadline_at` (A11): data fixa.
    vi.setSystemTime(new Date("2026-09-16T10:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("mentre carrega pinta l'esquelet amb el títol i les columnes reals, sense recomptes", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith({ "/companies/c1/workspaces": pending, "/companies/c1/matches/summary": summary });
    const { container } = render(<WorkspacesPage />);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Candidatures" })).toBeTruthy();
    for (const header of HEADERS) expect(screen.getByText(header)).toBeTruthy();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\b0 candidatures\b/);
  });

  it("agrupa per pas, ordena per termini, separa recomanació i decisió i marca què demana atenció", async () => {
    respondWith({
      "/companies/c1/workspaces": [
        workspace({ id: "far", tender_title: "Manteniment de xarxa", days_remaining: 31, deadline_at: "2026-10-17T12:00:00Z" }),
        workspace({ id: "urgent", tender_title: "Fibra òptica municipal", days_remaining: 6, deadline_at: "2026-09-22T12:00:00Z", tender_lot_id: "lot-1", analysis_outdated: true }),
        workspace({ id: "prep", tender_title: "Energia elèctrica", status: "preparing_bid", human_decision: "go", recommendation: "no_go", eligibility: "no_go", days_remaining: 20 }),
        workspace({ id: "done", tender_title: "Neteja viària", status: "submitted", human_decision: "go", days_remaining: -4, deadline_passed: true, deadline_at: "2026-09-12T12:00:00Z" }),
        workspace({ id: "nogo", tender_title: "Consultoria", status: "no_go_approved", human_decision: "no_go" }),
      ],
      "/companies/c1/matches/summary": summary,
    });
    const { container } = render(<WorkspacesPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();

    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent?.trim());
    expect(headers).toEqual(HEADERS);

    // Grups en ordre; «Tancades» plegat.
    const groupButtons = [...container.querySelectorAll("tbody button[aria-expanded]")];
    expect(groupButtons.map((button) => button.querySelector("span")?.textContent?.replace(/\s+/g, " ").trim())).toEqual([
      "Per decidir · 2",
      "Preparant · 1",
      "Tancades · 2",
    ]);
    expect(groupButtons.map((button) => button.getAttribute("aria-expanded"))).toEqual(["true", "true", "false"]);
    const titles = [...container.querySelectorAll("tbody p.font-medium")].map((p) => p.textContent);
    expect(titles).toEqual(["Fibra òptica municipal", "Manteniment de xarxa", "Energia elèctrica"]);

    // Fila urgent: lot, termini amb nivell, decisió pendent (no «PENDENT»), anàlisi endarrerida.
    const urgent = screen.getByText("Fibra òptica municipal").closest("tr")!;
    expect(urgent.textContent).toContain("Per lot");
    const pills = [...urgent.querySelectorAll("[data-level]")].map((pill) => `${pill.textContent?.trim()}[${pill.getAttribute("data-level")}]`);
    expect(pills).toContain("6 dies[blocking]");
    expect(pills).toContain("Pendent de decidir[attention]");
    expect(pills).toContain("Pendent de decisió[attention]");
    expect(pills).toContain("Anàlisi endarrerida[attention]");
    expect(urgent.textContent).not.toContain("PENDENT");
    expect(urgent.textContent).toContain("Candidata");
    const urgentAction = [...urgent.querySelectorAll("a[href='/candidatures/urgent']")].pop()!;
    expect(urgentAction.textContent).toContain("Decideix");
    expect(urgentAction.getAttribute("data-variant")).toBe("primary");

    // Resultat de l'anàlisi amb bloqueig i decisió humana GO: dues columnes, dos valors,
    // i cap «NO-GO» automàtic (A14): el NO-GO només el diu una persona.
    const prep = screen.getByText("Energia elèctrica").closest("tr")!;
    const prepPills = [...prep.querySelectorAll("[data-level]")].map((pill) => `${pill.textContent?.trim()}[${pill.getAttribute("data-level")}]`);
    expect(prep.textContent).toContain("Bloqueig confirmat");
    expect(prep.textContent).not.toContain("NO-GO");
    expect(prepPills).toContain("GO[ready]");
    expect(prepPills).toContain("Elegibilitat: No elegible[blocking]");
    expect(prepPills).toContain("Preparant oferta[running]");
    expect([...prep.querySelectorAll("a[href='/candidatures/prep']")].pop()?.textContent).toContain("Prepara");

    // Desplegar «Tancades»: els dos estats tancats continuen sent diferents.
    fireEvent.click(screen.getByRole("button", { name: /Tancades/ }));
    const done = screen.getByText("Neteja viària").closest("tr")!;
    expect([...done.querySelectorAll("[data-level]")].map((pill) => pill.textContent?.trim())).toContain("Presentada");
    const nogo = screen.getByText("Consultoria").closest("tr")!;
    expect([...nogo.querySelectorAll("[data-level]")].map((pill) => pill.textContent?.trim())).toContain("NO-GO aprovat");

    expect(screen.getByText("5 candidatures · 3 obertes · 2 tancades")).toBeTruthy();
    expect(container.textContent).not.toMatch(/recomanaci/i);
    expect(screen.getByText(/El resultat de l.anàlisi és automàtic; la decisió sempre és d.una persona./)).toBeTruthy();
  });

  it("l'estat «analysis» s'etiqueta segons `analysis_state` de la fila (A13)", async () => {
    respondWith({
      "/companies/c1/workspaces": [
        workspace({ id: "none", tender_title: "Sense anàlisi", status: "analysis", analysis_state: "absent" }),
        workspace({ id: "old", tender_title: "Anàlisi vella", status: "analysis", analysis_state: "stale" }),
        workspace({ id: "live", tender_title: "Anàlisi viva", status: "analysis", analysis_state: "current" }),
      ],
      "/companies/c1/matches/summary": summary,
    });
    render(<WorkspacesPage />);
    await screen.findByText("Sense anàlisi");
    expect(screen.getByText("Sense anàlisi").closest("tr")!.textContent).toContain("Pendent d'anàlisi");
    expect(screen.getByText("Anàlisi vella").closest("tr")!.textContent).toContain("Anàlisi obsoleta");
    expect(screen.getByText("Anàlisi viva").closest("tr")!.textContent).toContain("En anàlisi");
  });

  it("la fila sencera obre la candidatura amb el ratolí i amb Enter", async () => {
    respondWith({ "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Licitació única" })], "/companies/c1/matches/summary": summary });
    const { container } = render(<WorkspacesPage />);
    await screen.findByText("Licitació única");
    const row = container.querySelector("tbody tr[tabindex='0']")!;
    fireEvent.click(row);
    expect(push).toHaveBeenCalledWith("/candidatures/w1");
    push.mockClear();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/candidatures/w1");
  });

  it("sense candidatures → B2 amb «Veure les N amb més encaix»", async () => {
    respondWith({ "/companies/c1/workspaces": [], "/companies/c1/matches/summary": summary });
    render(<WorkspacesPage />);
    expect(await screen.findByRole("heading", { name: "Cap candidatura oberta" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure les 12 amb més encaix" }).getAttribute("href")).toBe("/licitacions?match_status=candidate");
  });

  it("sense candidatures i amb el resum caigut → «Veure Oportunitats», cap zero", async () => {
    respondWith({ "/companies/c1/workspaces": [], "/companies/c1/matches/summary": new Error("503") });
    const { container } = render(<WorkspacesPage />);
    expect(await screen.findByRole("heading", { name: "Cap candidatura oberta" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure Oportunitats" }).getAttribute("href")).toBe("/licitacions");
    expect(container.textContent).not.toMatch(/\b0 amb més encaix/);
  });

  it("si la llista falla mostra l'error E1 amb reintent", async () => {
    respondWith({ "/companies/c1/workspaces": new Error("503"), "/companies/c1/matches/summary": summary });
    render(<WorkspacesPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar les candidatures" })).toBeTruthy();
    const calls = get.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(get.mock.calls.length).toBeGreaterThan(calls);
  });
});
