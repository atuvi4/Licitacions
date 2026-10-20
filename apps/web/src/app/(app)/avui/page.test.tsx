import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TenderMatchListItem } from "@/lib/matching";
import type { WorkspaceSummary } from "@/lib/workspace";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) },
}));
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1" }) }));

import AvuiPage from "./page";

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

function matchItem(id: string, title: string, deadline: string | null): TenderMatchListItem {
  return {
    tender: {
      id,
      external_id: id,
      expediente_number: "EXP-" + id,
      title,
      status: "open",
      procedure_type: "open",
      contract_type: "services",
      estimated_value: "240000",
      base_budget: null,
      currency: "EUR",
      publication_date: null,
      deadline_at: deadline,
      nuts_code: null,
      place_of_execution: null,
      buyer: { id: "b", name: "Ajuntament de Sabadell", tax_identifier: null, city: null, nuts_code: null },
      cpvs: [],
      lot_count: 0,
      source_publication_count: 1,
      source_code: null,
      is_modified: false,
    },
    match: {
      id: "m-" + id,
      tender_id: id,
      score: 92,
      status: "candidate",
      exclusion_kind: null,
      exclusion_reason_code: null,
      cpv_score: 40,
      geographic_score: 25,
      economic_score: 25,
      timing_score: 2,
      data_coverage: 1,
      factors: [
        { dimension: "cpv", result: "match", reason_code: "CPV_EXACT_HIGH_PRIORITY", weight: 40, score: 40, max_score: 40, company_value: null, source_value: null, detail: {} },
        { dimension: "timing", result: "partial", reason_code: "DEADLINE_SHORT", weight: 10, score: 2, max_score: 10, company_value: null, source_value: null, detail: {} },
      ],
      rules_version: "v1",
      calculated_at: "2026-09-15T00:00:00Z",
      best_lot_id: null,
      candidate_lot_count: 0,
      total_lot_count: 0,
      is_stale: false,
      lot_matches: [],
    },
  };
}

const FULL_PROFILE = { cpv_preferences: 9, territories: 1, contract_preferences: 1 };
const EMPTY_PROFILE = { cpv_preferences: 0, territories: 0, contract_preferences: 0 };
// P2-09: els recomptes del perfil viatgen amb el resum del Radar; Avui ja no demana `/summary`.
const summary = { total_tenders: 148, evaluated_count: 148, actionable: 100, candidates: 12, high_fit: 5, low_fit: 3, excluded: 1, closed: 2, insufficient_data: 1, stale: 0, rules_version: "v1", last_calculated_at: null, profile_counts: FULL_PROFILE, open_candidates_with_workspace: 0 };
const noFresh = { items: [], total: 0, limit: 5, offset: 0 };

/** La resposta de `…/workspaces/preparation-counts`. */
function counts(pending: Record<string, number | null>) {
  return { items: Object.entries(pending).map(([workspace_id, value]) => ({ workspace_id, pending: value })) };
}

const PREPARATION = "/companies/c1/workspaces/preparation-counts";

function respondWith(map: Record<string, unknown | Deferred | Error>) {
  get.mockImplementation((path: string) => {
    const key = Object.keys(map).find((prefix) => path.startsWith(prefix));
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

const calledPaths = () => get.mock.calls.map(([path]) => String(path));

function section(title: string): HTMLElement {
  return screen.getByText(title).closest("section")!;
}

function titles(container: HTMLElement): string[] {
  return [...container.querySelectorAll("p.font-medium")].map((p) => p.textContent ?? "");
}

describe("Avui", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("mentre carrega pinta l'esquelet amb el títol real i cap recompte", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith({ "/companies/c1/workspaces": pending, "/companies/c1/matches?": pending, "/companies/c1/matches/summary": summary });
    const { container } = render(<AvuiPage />);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Avui" })).toBeTruthy();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\b0 decisions\b/);
    expect(screen.queryByText("0")).toBeNull();
  });

  it("amb dades: tres blocs ordenats, primera fila amb el primari i la recomanació com a text", async () => {
    respondWith({
      [PREPARATION]: counts({ far: 0, urgent: 0, blocked: 0 }),
      "/companies/c1/workspaces": [
        workspace({ id: "far", tender_title: "Manteniment de xarxa", days_remaining: 31 }),
        workspace({ id: "urgent", tender_title: "Fibra òptica municipal", days_remaining: 6, deadline_at: "2026-09-22T12:00:00Z" }),
        workspace({ id: "blocked", tender_title: "Energia elèctrica", human_decision: "go", status: "preparing_bid", eligibility: "no_go", days_remaining: 20 }),
      ],
      "/companies/c1/matches?": { items: [matchItem("t9", "Connectivitat per a centres educatius", "2026-10-30T12:00:00Z")], total: 12, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const decisionsCard = section("Decisions pendents");
    expect(titles(decisionsCard)).toEqual(["Fibra òptica municipal", "Manteniment de xarxa"]);
    const buttons = [...decisionsCard.querySelectorAll("a[href^='/candidatures/']")];
    expect(buttons[0].getAttribute("href")).toBe("/candidatures/urgent?pas=decidir");
    expect(buttons[0].getAttribute("data-variant")).toBe("primary");
    expect(buttons[1].getAttribute("data-variant")).not.toBe("primary");
    // A14: el motor dona un resultat, no una recomanació.
    expect(decisionsCard.textContent).toContain("Resultat de l'anàlisi: Candidata");
    expect(decisionsCard.textContent).not.toMatch(/Recomanaci/);
    expect(decisionsCard.querySelector("button, a[role='button']")?.textContent ?? "").not.toContain("Candidata");
    const blockers = section("Bloquejos i pendents");
    expect(blockers.textContent).toContain("Energia elèctrica");
    expect(blockers.textContent).toContain("No elegible");
    expect(blockers.textContent).not.toContain("NO-GO");
    const fresh = section("Candidates del Radar");
    expect(fresh.textContent).toContain("Connectivitat per a centres educatius");
    expect(fresh.querySelector("a[href='/licitacions/t9']")).not.toBeNull();
    // A17: candidates prioritzades pel Radar sobre el total de licitacions, mai «oportunitats noves».
    expect(fresh.textContent).toContain("prioritzades pel Radar, no licitacions ingerides");
    expect(screen.getByText(/2 decisions pendents, 1 bloqueig detectat, 12 candidates de 148 licitacions/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/oportunitats noves|Noves per revisar/);
    expect(screen.getByRole("link", { name: /Veure les 12 a Oportunitats/ }).getAttribute("href")).toBe("/licitacions?match_status=candidate");
  });

  it("F1: amb el perfil complet però cap licitació avaluada diu que el Radar no s'ha calculat i ofereix calcular-lo", async () => {
    respondWith({
      "/companies/c1/workspaces": [],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 46, evaluated_count: 0, actionable: 0, low_fit: 0, excluded: 0, closed: 0, insufficient_data: 0, high_fit: 0 },
    });
    post.mockResolvedValue({ tenders_evaluated: 46, lots_evaluated: 0, created: 46, updated: 0, duration_ms: 10, rules_version: "v1" });
    render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "El Radar encara no s'ha calculat" })).toBeTruthy();
    expect(screen.getByText(/0 candidates de 46 licitacions: el Radar encara no s'ha calculat/)).toBeTruthy();
    // unknown ≠ no: cap «cap coincidència», cap culpa al perfil, cap «amplia».
    expect(document.body.textContent).not.toMatch(/cap coincidència|encaixa amb el perfil|no té CPV|amplia/);
    expect(screen.getByText(/Radar encara no calculat \(46 licitacions\)/)).toBeTruthy();
    const calls = get.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Calcula el pre-fit" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/matches/recalculate", {});
    expect(get.mock.calls.length).toBeGreaterThan(calls);
  });

  it("F1: amb candidatures i el Radar sense calcular, el bloc del Radar no diu «cap coincidència» i ofereix el càlcul", async () => {
    respondWith({
      [PREPARATION]: counts({ w1: 0 }),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 46, evaluated_count: 0 },
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const radar = section("Candidates del Radar");
    expect(radar.textContent).toContain("0 candidates de 46 licitacions: el Radar encara no s'ha calculat");
    expect(radar.textContent).not.toMatch(/cap coincidència|no té CPV/);
    expect(radar.querySelector("button")?.textContent).toBe("Calcula el pre-fit");
  });

  it("sense cap candidatura ni cap candidata: «0 candidates de N licitacions» amb la causa del perfil i la sortida al Radar (A17, B1)", async () => {
    respondWith({
      "/companies/c1/workspaces": [],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336, profile_counts: EMPTY_PROFILE },
    });
    render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "Encara no sabem què busqueu" })).toBeTruthy();
    expect(screen.getByText(/0 candidates de 17\.336 licitacions: el perfil no té CPV\/territoris/)).toBeTruthy();
    expect(document.body.textContent).toContain("prioritzades pel Radar, no licitacions ingerides");
    // Carry-forward del lot 9B: cap «Completa el perfil» cap a /empresa; la sortida són les Preferències del Radar.
    expect(screen.queryByRole("link", { name: /Completa el perfil/ })).toBeNull();
    expect(screen.getByRole("link", { name: /Defineix les preferències del Radar/ }).getAttribute("href")).toBe("/empresa/radar");
    expect(document.body.textContent).not.toMatch(/\d+ ?% /);
  });

  it("amb perfil i licitacions avaluades però cap candidata, la causa és «cap coincidència» entre les avaluades", async () => {
    respondWith({
      "/companies/c1/workspaces": [],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 120 },
    });
    render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "Encara no hi ha res a la cua" })).toBeTruthy();
    expect(screen.getByText(/0 candidates de 17\.336 licitacions: cap coincidència entre les 120 avaluades/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Completa el perfil/ })).toBeNull();
  });

  it("amb candidatures però cap candidata, el bloc del Radar explica el 0 amb la causa i porta al Radar", async () => {
    respondWith({
      [PREPARATION]: counts({ w1: 0 }),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336, profile_counts: EMPTY_PROFILE },
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const radar = section("Candidates del Radar");
    expect(radar.textContent).toContain("0 candidates de 17.336 licitacions: el perfil no té CPV/territoris");
    expect(radar.querySelector("a[href='/empresa/radar']")).not.toBeNull();
    expect(screen.getByText(/1 decisió pendent, 0 bloquejos detectats, 0 candidates de 17\.336 licitacions/)).toBeTruthy();
  });

  it("P2-05: L'Escala (oberta, sense decisió, sense anàlisi, 4 pendents) surt una sola vegada, a «Decisions pendents», amb les etiquetes", async () => {
    respondWith({
      [PREPARATION]: counts({ lescala: 4 }),
      "/companies/c1/workspaces": [
        workspace({
          id: "lescala",
          tender_title: "Telecomunicacions 2027",
          status: "analysis",
          eligibility: null,
          recommendation: null,
          human_decision: null,
          analysis_state: "absent",
          days_remaining: 1,
        }),
      ],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336 },
    });
    render(<AvuiPage />);
    await screen.findByText(/4 pendents de preparació/);

    expect(titles(document.body).filter((title) => title === "Telecomunicacions 2027")).toHaveLength(1);
    const decisions = section("Decisions pendents");
    expect(decisions.textContent).toContain("Telecomunicacions 2027");
    expect(decisions.querySelector("[data-testid='queue-tags']")?.textContent).toBe(
      "Sense avaluar · 4 pendents de preparació (totes les àrees)",
    );
    expect(decisions.querySelector("a[href='/candidatures/lescala?pas=decidir']")).not.toBeNull();

    const blockers = section("Bloquejos i pendents");
    expect(blockers.textContent).not.toContain("Telecomunicacions 2027");
    expect(blockers.textContent).not.toContain("Sense anàlisi del plec");
    expect(blockers.querySelector("a[href='/candidatures/lescala?pas=tancar']")).toBeNull();
    // L'advertència de P1-04 compta totes les sense avaluar, siguin on siguin.
    expect(blockers.textContent).toContain(
      "Cap bloqueig detectat. Això no vol dir que no n'hi hagi: 1 candidatura encara no s'ha avaluat.",
    );
    expect(blockers.textContent).toContain("Cap altre pendent de preparació: 1 candidatura amb pendents surt més amunt.");
    expect(blockers.textContent).not.toContain("Cap pendent de preparació a les candidatures obertes.");
    // La línia del dia compta per secció assignada: la sense avaluar ja és una decisió pendent.
    expect(screen.getByText(/1 decisió pendent, 0 bloquejos detectats, 0 candidates de 17\.336 licitacions/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/candidatura sense avaluar|0 avisos/);
  });

  it("P2-05: un NO-GO sense decidir és un bloqueig amb l'etiqueta «Decisió pendent», i «Decisions pendents» ho diu", async () => {
    respondWith({
      [PREPARATION]: counts({ nogo: 0 }),
      "/companies/c1/workspaces": [
        workspace({ id: "nogo", tender_title: "Energia elèctrica", eligibility: "no_go", analysis_state: "current", days_remaining: 20 }),
      ],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    const blockers = (await screen.findByText("Bloquejos i pendents")).closest("section")!;
    await screen.findByText("Cap pendent de preparació a les candidatures obertes.");
    expect(blockers.textContent).toContain("No elegible");
    expect(blockers.querySelector("[data-testid='queue-tags']")?.textContent).toBe("Decisió pendent");
    expect(blockers.textContent).not.toContain("Sense avaluar");
    expect(blockers.textContent).not.toContain("Cap bloqueig detectat");
    expect(section("Decisions pendents").textContent).toContain(
      "Cap altra decisió pendent: 1 amb un bloqueig detectat (a «Bloquejos i pendents»).",
    );
    expect(screen.getByText(/0 decisions pendents, 1 bloqueig detectat/)).toBeTruthy();
  });

  it("P2-05: comptadors únics per secció assignada i cada candidatura una sola vegada", async () => {
    respondWith({
      [PREPARATION]: counts({ x: 2, y: 3, z: 1, v: 5, u: 0 }),
      "/companies/c1/workspaces": [
        workspace({ id: "x", tender_title: "Bloqueig", eligibility: "no_go", analysis_state: "absent", days_remaining: 4 }),
        workspace({ id: "y", tender_title: "Decisió", analysis_state: "absent", days_remaining: 5 }),
        workspace({ id: "z", tender_title: "Per avaluar", human_decision: "go", status: "preparing_bid", analysis_state: "absent", days_remaining: 6 }),
        workspace({ id: "v", tender_title: "Per preparar", human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: 7 }),
        workspace({ id: "u", tender_title: "Res a fer", human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: 8 }),
      ],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    await screen.findByText("Per preparar");

    const all = titles(document.body);
    for (const title of ["Bloqueig", "Decisió", "Per avaluar", "Per preparar"]) {
      expect(all.filter((value) => value === title)).toHaveLength(1);
    }
    expect(all).not.toContain("Res a fer");
    const decisions = section("Decisions pendents");
    const blockers = section("Bloquejos i pendents");
    expect(decisions.querySelector("[data-level='neutral']")?.textContent).toBe("1");
    expect(blockers.querySelector("[data-level='neutral']")?.textContent).toBe("1");
    expect(titles(decisions)).toEqual(["Decisió"]);
    expect(titles(blockers)).toEqual(["Bloqueig", "Per avaluar", "Per preparar"]);
    const tags = [...document.querySelectorAll("[data-testid='queue-tags']")].map((node) => node.textContent);
    expect(tags).toEqual([
      "Sense avaluar · 3 pendents de preparació (totes les àrees)",
      "Decisió pendent · Sense avaluar · 2 pendents de preparació (totes les àrees)",
      "1 pendent de preparació (totes les àrees)",
    ]);
    expect(screen.getByText(/1 decisió pendent, 1 bloqueig detectat, 1 candidatura sense avaluar/)).toBeTruthy();
  });

  it("auditoria UI P1-04: si no es poden comptar els pendents, no s'afirma cap zero", async () => {
    respondWith({
      [PREPARATION]: new Error("503"),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", human_decision: "go", status: "preparing_bid", analysis_state: "current" })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Sense comptar")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Cap pendent de preparació");
  });

  it("una candidatura que no surt a la resposta queda sense comptar, mai a zero", async () => {
    respondWith({
      [PREPARATION]: counts({}),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", human_decision: "go", status: "preparing_bid", analysis_state: "current" })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Sense comptar")).toBeTruthy();
  });

  it("revisió del patch: «Torna-ho a comptar» torna a demanar els recomptes encara que les candidatures siguin les mateixes", async () => {
    let fail = true;
    respondWith({
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", human_decision: "go", status: "preparing_bid", analysis_state: "current" })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    const base = get.getMockImplementation()!;
    get.mockImplementation((path: string) => {
      if (path === PREPARATION) {
        return fail ? Promise.reject(new Error("503")) : Promise.resolve(counts({ w1: 1 }));
      }
      return base(path);
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Sense comptar")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a comptar: Fibra òptica municipal" }));
    expect(await screen.findByText(/1 pendent de preparació/)).toBeTruthy();
    expect(screen.queryByText("Sense comptar")).toBeNull();
    expect(calledPaths().filter((path) => path === PREPARATION)).toHaveLength(2);
  });

  it("P2-09: una sola crida de recomptes per a totes les candidatures, cap final-readiness i cap /summary", async () => {
    const ids = ["a", "b", "c", "d", "e"];
    respondWith({
      [PREPARATION]: counts(Object.fromEntries(ids.map((id) => [id, 0]))),
      "/companies/c1/workspaces": ids.map((id, index) => workspace({ id, tender_title: `Licitació ${id}`, human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: index + 1 })),
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Cap pendent de preparació a les candidatures obertes.")).toBeTruthy();
    const paths = calledPaths();
    expect(paths.filter((path) => path === PREPARATION)).toHaveLength(1);
    expect(paths.filter((path) => path.endsWith("/final-readiness"))).toHaveLength(0);
    expect(paths.filter((path) => path === "/companies/c1/summary" || path.startsWith("/companies/c1/summary?"))).toHaveLength(0);
  });

  it("sense cap candidatura a l'abast no es demanen recomptes", async () => {
    respondWith({
      "/companies/c1/workspaces": [workspace({ id: "sub", status: "submitted", human_decision: "go", analysis_state: "current" })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Cap pendent de preparació a les candidatures obertes.")).toBeTruthy();
    expect(calledPaths()).not.toContain(PREPARATION);
  });

  it("revisió final: una candidatura d'una altra empresa no surt a «Sense avaluar», ni a les decisions, ni a la línia del dia", async () => {
    respondWith({
      [PREPARATION]: counts({ mine: 0 }),
      "/companies/c1/workspaces": [
        workspace({ id: "mine", tender_title: "La meva", analysis_state: "current" }),
        workspace({ id: "other", company_id: "c2", tender_title: "D'una altra empresa", analysis_state: "absent" }),
      ],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": summary,
    });
    render(<AvuiPage />);
    const blockers = (await screen.findByText("Bloquejos i pendents")).closest("section")!;
    await screen.findByText("Cap pendent de preparació a les candidatures obertes.");
    expect(blockers.textContent).not.toContain("Sense avaluar");
    expect(document.body.textContent).not.toContain("D'una altra empresa");
    expect(document.body.textContent).not.toMatch(/sense avaluar,/);
    expect(screen.getByText(/1 decisió pendent, 0 bloquejos detectats/)).toBeTruthy();
  });

  it("P2-05: el Radar demana només les candidates noves i diu a part les que ja tenen candidatura", async () => {
    respondWith({
      [PREPARATION]: counts({ w1: 0 }),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": { items: [matchItem("t9", "Connectivitat per a centres educatius", "2026-10-30T12:00:00Z")], total: 4, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, open_candidates_with_workspace: 2 },
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Connectivitat per a centres educatius")).toBeTruthy();
    const feed = calledPaths().find((path) => path.startsWith("/companies/c1/matches?"))!;
    const params = new URLSearchParams(feed.split("?")[1]);
    expect(params.get("exclude_open_workspaces")).toBe("true");
    expect(params.get("match_status")).toBe("candidate");
    expect(params.get("only_open")).toBe("true");
    expect(params.get("limit")).toBe("5");
    const radar = section("Candidates del Radar");
    expect(screen.getByRole("link", { name: "2 candidates ja tenen candidatura" }).getAttribute("href")).toBe("/candidatures");
    // «Veure les N»: noves + amb candidatura; la píndola de la capçalera no canvia.
    expect(screen.getByRole("link", { name: /Veure les 6 a Oportunitats/ })).toBeTruthy();
    expect(radar.textContent).toContain("12 candidates de 148 licitacions");
  });

  it("P2-05: amb una sola candidata amb candidatura el text va en singular", async () => {
    respondWith({
      [PREPARATION]: counts({ w1: 0 }),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": { items: [matchItem("t9", "Connectivitat per a centres educatius", null)], total: 1, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, open_candidates_with_workspace: 1 },
    });
    render(<AvuiPage />);
    expect(await screen.findByRole("link", { name: "1 candidata ja té candidatura" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Veure les 2 a Oportunitats/ })).toBeTruthy();
  });

  it("P2-05: si totes les candidates obertes ja tenen candidatura, el Radar ho diu (no «cap candidata»)", async () => {
    respondWith({
      [PREPARATION]: counts({ w1: 0 }),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": noFresh,
      "/companies/c1/matches/summary": { ...summary, candidates: 3, open_candidates_with_workspace: 3 },
    });
    render(<AvuiPage />);
    await screen.findByText("Fibra òptica municipal");
    const radar = section("Candidates del Radar");
    expect(radar.textContent).toContain("Totes les candidates obertes ja tenen candidatura (3).");
    expect(radar.textContent).not.toContain("Cap candidata amb termini obert");
    expect(radar.querySelector("a[href='/candidatures']")).not.toBeNull();
  });

  it("si la càrrega falla mostra l'error amb reintent, mai un zero", async () => {
    respondWith({ "/companies/c1/workspaces": new Error("503"), "/companies/c1/matches?": new Error("503"), "/companies/c1/matches/summary": new Error("503") });
    const { container } = render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar la feina d'avui" })).toBeTruthy();
    expect(container.textContent).not.toMatch(/\b0\b/);
    const calls = get.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(get.mock.calls.length).toBeGreaterThan(calls);
    expect(calledPaths().some((path) => path.startsWith("/companies/c1/summary"))).toBe(false);
  });
});
