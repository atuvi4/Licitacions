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

const summary = { total_tenders: 148, evaluated_count: 148, actionable: 100, candidates: 12, high_fit: 5, low_fit: 3, excluded: 1, closed: 2, insufficient_data: 1, stale: 0, rules_version: "v1", last_calculated_at: null };
const profile = { company: { id: "c1", legal_name: "Demo", trade_name: null }, completeness: { score: 96, sections: [] }, counts: { services: 1, territories: 1, financial_years: 1, certifications: 1, registrations: 1, insurances: 1, people: 1, qualifications: 1, cpv_preferences: 9, projects: 1, documents: 1, document_links: 1 }, expiry_alerts: [] };

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

describe("Avui", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("mentre carrega pinta l'esquelet amb el títol real i cap recompte", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith({ "/companies/c1/workspaces": pending, "/companies/c1/matches?": pending, "/companies/c1/matches/summary": summary, "/companies/c1/summary": profile });
    const { container } = render(<AvuiPage />);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Avui" })).toBeTruthy();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\b0 decisions\b/);
    expect(screen.queryByText("0")).toBeNull();
  });

  it("amb dades: tres blocs ordenats, primera fila amb el primari i la recomanació com a text", async () => {
    respondWith({
      "/companies/c1/workspaces": [
        workspace({ id: "far", tender_title: "Manteniment de xarxa", days_remaining: 31 }),
        workspace({ id: "urgent", tender_title: "Fibra òptica municipal", days_remaining: 6, deadline_at: "2026-09-22T12:00:00Z" }),
        workspace({ id: "blocked", tender_title: "Energia elèctrica", human_decision: "go", status: "preparing_bid", eligibility: "no_go", days_remaining: 20 }),
      ],
      "/companies/c1/matches?": { items: [matchItem("t9", "Connectivitat per a centres educatius", "2026-10-30T12:00:00Z")], total: 12, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": summary,
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const decisionsCard = screen.getByText("Decisions pendents").closest("section")!;
    const titles = [...decisionsCard.querySelectorAll("p.font-medium")].map((p) => p.textContent);
    expect(titles).toEqual(["Fibra òptica municipal", "Manteniment de xarxa"]);
    const buttons = [...decisionsCard.querySelectorAll("a[href^='/candidatures/']")];
    expect(buttons[0].getAttribute("href")).toBe("/candidatures/urgent?pas=decidir");
    expect(buttons[0].getAttribute("data-variant")).toBe("primary");
    expect(buttons[1].getAttribute("data-variant")).not.toBe("primary");
    // A14: el motor dona un resultat, no una recomanació.
    expect(decisionsCard.textContent).toContain("Resultat de l'anàlisi: Candidata");
    expect(decisionsCard.textContent).not.toMatch(/Recomanaci/);
    expect(decisionsCard.querySelector("button, a[role='button']")?.textContent ?? "").not.toContain("Candidata");
    const blockers = screen.getByText("Bloquejos i pendents").closest("section")!;
    expect(blockers.textContent).toContain("Energia elèctrica");
    expect(blockers.textContent).toContain("No elegible");
    expect(blockers.textContent).not.toContain("NO-GO");
    const fresh = screen.getByText("Candidates del Radar").closest("section")!;
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
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 46, evaluated_count: 0, actionable: 0, low_fit: 0, excluded: 0, closed: 0, insufficient_data: 0, high_fit: 0 },
      "/companies/c1/summary": profile,
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
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 46, evaluated_count: 0 },
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const radar = screen.getByText("Candidates del Radar").closest("section")!;
    expect(radar.textContent).toContain("0 candidates de 46 licitacions: el Radar encara no s'ha calculat");
    expect(radar.textContent).not.toMatch(/cap coincidència|no té CPV/);
    expect(radar.querySelector("button")?.textContent).toBe("Calcula el pre-fit");
  });

  it("sense cap candidatura ni cap candidata: «0 candidates de N licitacions» amb la causa del perfil i la sortida al Radar (A17, B1)", async () => {
    respondWith({
      "/companies/c1/workspaces": [],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336 },
      "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0, territories: 0, contract_preferences: 0 } },
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
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 120 },
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "Encara no hi ha res a la cua" })).toBeTruthy();
    expect(screen.getByText(/0 candidates de 17\.336 licitacions: cap coincidència entre les 120 avaluades/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Completa el perfil/ })).toBeNull();
  });

  it("amb candidatures però cap candidata, el bloc del Radar explica el 0 amb la causa i porta al Radar", async () => {
    respondWith({
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", days_remaining: 6 })],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336 },
      "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0, territories: 0, contract_preferences: 0 } },
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Fibra òptica municipal")).toBeTruthy();
    const radar = screen.getByText("Candidates del Radar").closest("section")!;
    expect(radar.textContent).toContain("0 candidates de 17.336 licitacions: el perfil no té CPV/territoris");
    expect(radar.querySelector("a[href='/empresa/radar']")).not.toBeNull();
    expect(screen.getByText(/1 decisió pendent, 0 bloquejos detectats, 0 candidates de 17\.336 licitacions/)).toBeTruthy();
  });

  it("auditoria UI P1-04: sense anàlisi no és «cap bloqueig»; es diu «sense avaluar» i els pendents de preparació", async () => {
    const blocker = (code: string) => ({ code, severity: "blocker", domain: "workspace", entity_id: null, context: {} });
    respondWith({
      "/companies/c1/workspaces/lescala/final-readiness": {
        findings: [
          blocker("technical_scope_unreviewed"),
          blocker("economic_lot_empty"),
          blocker("decision_not_go"),
          blocker("analysis_missing"),
        ],
      },
      "/companies/c1/workspaces": [
        workspace({
          id: "lescala",
          tender_title: "Telecomunicacions 2027",
          status: "analysis",
          eligibility: null,
          recommendation: null,
          human_decision: null,
          analysis_state: "absent",
          days_remaining: 6,
        }),
      ],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": { ...summary, candidates: 0, total_tenders: 17336, evaluated_count: 17336 },
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    const section = (await screen.findByText("Bloquejos i pendents")).closest("section")!;
    await screen.findByText(/4 pendents de preparació/);
    expect(section.textContent).not.toContain("Cap bloqueig obert");
    expect(section.textContent).toContain(
      "Cap bloqueig detectat. Això no vol dir que no n'hi hagi: 1 candidatura encara no s'ha avaluat.",
    );
    expect(section.textContent).toContain("Sense avaluar");
    expect(section.textContent).toContain("Sense anàlisi del plec");
    expect(section.textContent).toContain("4 pendents de preparació (totes les àrees)");
    expect(section.querySelector("a[href='/candidatures/lescala?pas=tancar']")).not.toBeNull();
    // La línia del dia: zero bloquejos detectats, però la candidatura sense avaluar es diu a part.
    expect(screen.getByText(/1 decisió pendent, 0 bloquejos detectats, 1 candidatura sense avaluar/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/0 avisos/);
  });

  it("auditoria UI P1-04: un NO-GO amb anàlisi és un bloqueig i no surt com a «sense avaluar»", async () => {
    respondWith({
      "/companies/c1/workspaces/nogo/final-readiness": { findings: [] },
      "/companies/c1/workspaces": [
        workspace({ id: "nogo", tender_title: "Energia elèctrica", eligibility: "no_go", analysis_state: "current", days_remaining: 20 }),
      ],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": summary,
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    const section = (await screen.findByText("Bloquejos i pendents")).closest("section")!;
    await screen.findByText("Cap pendent de preparació a les candidatures obertes.");
    expect(section.textContent).toContain("No elegible");
    expect(section.textContent).not.toContain("Sense avaluar");
    expect(section.textContent).not.toContain("Cap bloqueig detectat");
  });

  it("auditoria UI P1-04: si no es poden comptar els pendents, no s'afirma cap zero", async () => {
    respondWith({
      "/companies/c1/workspaces/w1/final-readiness": new Error("503"),
      "/companies/c1/workspaces": [workspace({ id: "w1", tender_title: "Fibra òptica municipal", analysis_state: "current" })],
      "/companies/c1/matches?": { items: [], total: 0, limit: 5, offset: 0 },
      "/companies/c1/matches/summary": summary,
      "/companies/c1/summary": profile,
    });
    render(<AvuiPage />);
    expect(await screen.findByText("Sense comptar")).toBeTruthy();
    expect(document.body.textContent).not.toContain("Cap pendent de preparació");
  });

  it("si la càrrega falla mostra l'error amb reintent, mai un zero", async () => {
    respondWith({ "/companies/c1/workspaces": new Error("503"), "/companies/c1/matches?": new Error("503"), "/companies/c1/matches/summary": new Error("503"), "/companies/c1/summary": new Error("503") });
    const { container } = render(<AvuiPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar la feina d'avui" })).toBeTruthy();
    expect(container.textContent).not.toMatch(/\b0\b/);
    const calls = get.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(get.mock.calls.length).toBeGreaterThan(calls);
  });
});
