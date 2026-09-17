import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchSummary, TenderMatchListItem } from "@/lib/matching";
import type { TenderFacets } from "@/lib/tenders";

const get = vi.fn();
const post = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("@/lib/api", () => ({
  api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) },
}));
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1" }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/licitacions",
}));

import TendersFeedPage from "./page";

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void };

function item(id: string, overrides: Partial<TenderMatchListItem["tender"]> = {}, match: Partial<NonNullable<TenderMatchListItem["match"]>> | null = {}): TenderMatchListItem {
  return {
    tender: {
      id,
      external_id: id,
      expediente_number: "EXP-" + id,
      title: "Licitació " + id,
      status: "open",
      procedure_type: "open",
      contract_type: "services",
      estimated_value: "240000",
      base_budget: null,
      currency: "EUR",
      publication_date: "2026-09-01",
      deadline_at: "2026-10-30T12:00:00Z",
      nuts_code: "ES511",
      place_of_execution: "Sabadell",
      buyer: { id: "b", name: "Ajuntament de Sabadell", tax_identifier: null, city: null, nuts_code: null },
      cpvs: [],
      lot_count: 0,
      source_publication_count: 1,
      source_code: "PLACSP",
      is_modified: false,
      ...overrides,
    },
    match:
      match === null
        ? null
        : {
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
            ...match,
          },
  };
}

const summary: MatchSummary = { total_tenders: 148, actionable: 100, candidates: 12, high_fit: 5, low_fit: 30, excluded: 4, closed: 40, insufficient_data: 2, stale: 0, rules_version: "v1", last_calculated_at: "2026-09-15T00:00:00Z" };
const facets: TenderFacets = { statuses: ["open", "awarded"], procedure_types: ["open"], contract_types: ["services", "works"], nuts_codes: ["ES511"], total_tenders: 148 };
const profile = { company: { id: "c1", legal_name: "Demo", trade_name: null }, completeness: { score: 96, sections: [] }, counts: { services: 1, territories: 1, financial_years: 1, certifications: 1, registrations: 1, insurances: 1, people: 1, qualifications: 1, cpv_preferences: 9, projects: 1, documents: 1, document_links: 1 }, expiry_alerts: [] };

const HEADERS = ["Encaix", "Licitació", "Termini", "Import", "Estat"];

function respondWith(map: Record<string, unknown | Deferred | Error | ((path: string) => unknown)>) {
  get.mockImplementation((path: string) => {
    const key = path in map ? path : Object.keys(map).find((prefix) => path.startsWith(prefix));
    let value = key ? map[key] : undefined;
    if (typeof value === "function") value = (value as (path: string) => unknown)(path);
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
const matchCalls = () => get.mock.calls.map((call) => String(call[0])).filter((path) => path.startsWith("/companies/c1/matches?"));
const lastQuery = () => matchCalls().at(-1)?.split("?")[1];

const page = (items: TenderMatchListItem[], total = items.length) => ({ items, total, limit: 25, offset: 0 });

function base(items: TenderMatchListItem[], total?: number, extra: Record<string, unknown> = {}) {
  return {
    "/companies/c1/matches/summary": summary,
    "/companies/c1/matches?": page(items, total),
    "/tenders/facets": facets,
    "/companies/c1/summary": profile,
    ...extra,
  };
}

describe("Oportunitats", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    push.mockReset();
    search = "";
  });

  it("mentre carrega pinta l'esquelet amb el títol i les cinc columnes reals, sense cap recompte", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith(base([], 0, { "/companies/c1/matches?": pending, "/companies/c1/matches/summary": pending }));
    const { container } = render(<TendersFeedPage />);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Oportunitats" })).toBeTruthy();
    for (const header of HEADERS) expect(screen.getByText(header)).toBeTruthy();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\b0 licitacions\b/);
  });

  it("amb dades: la consulta antiga, cinc columnes, vistes amb recompte i peu amb pàgines", async () => {
    respondWith(base([item("t1"), item("t2", { deadline_at: null, estimated_value: null, is_modified: true, source_publication_count: 3, lot_count: 2 }, { score: 58, status: "low_fit" })], 148));
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByText("Licitació t1")).toBeTruthy();
    expect(lastQuery()).toBe("sort=score&limit=25&offset=0");

    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent?.trim());
    expect(headers).toEqual(HEADERS);

    // Vistes: pastilles outline; l'activa és «action»; «Per revisar» = candidate amb recompte real.
    const views = screen.getAllByRole("button", { pressed: true });
    expect(views).toHaveLength(1);
    expect(views[0].textContent).toContain("Totes");
    expect(views[0].querySelector("[data-level='action'][data-outline='true']")).not.toBeNull();
    const review = screen.getByRole("button", { name: /Per revisar/ });
    expect(review.textContent).toContain("12");
    expect(review.getAttribute("aria-pressed")).toBe("false");

    // Files: encaix, motiu, termini amb nivell, import, estat.
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("92");
    expect(rows[0].textContent).toContain("El termini és curt");
    expect(rows[0].querySelector("[data-level]")).not.toBeNull();
    expect(rows[0].textContent).toContain("240.000");
    expect(rows[0].textContent).toContain("En termini");
    expect(rows[0].textContent).toContain("Ajuntament de Sabadell");
    expect(rows[1].textContent).toContain("Sense termini");
    expect(rows[1].textContent).toContain("Publicada 3 cops");
    expect(rows[1].textContent).toContain("2 lots");
    expect(rows[1].textContent).toContain("—");
    expect(rows[0].textContent).not.toMatch(/probabilitat/i);
    expect(container.textContent).toContain("no és una probabilitat de guanyar");

    expect(screen.getByText("148 licitacions · pàgina 1 de 6")).toBeTruthy();
    expect(container.textContent).toContain("v1");
    expect(container.textContent).toContain("PLACSP");
    // El select d'ordenació té etiqueta.
    expect(screen.getByLabelText("Ordena per")).toBeTruthy();
  });

  it("canviar de vista, d'ordre i de pàgina genera les mateixes queries que abans", async () => {
    respondWith(base([item("t1")], 60));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.click(screen.getByRole("button", { name: /Per revisar/ }));
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=score&limit=25&offset=0");
    fireEvent.change(screen.getByLabelText("Ordena per"), { target: { value: "deadline" } });
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=deadline&limit=25&offset=0");
    fireEvent.click(screen.getByRole("button", { name: "Següent" }));
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=deadline&limit=25&offset=25");
    expect(screen.getByText("60 licitacions · pàgina 2 de 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Fora de termini/ }));
    await flush();
    expect(lastQuery()).toBe("match_status=closed&sort=deadline&limit=25&offset=0");
  });

  it("llegeix ?q= i ?match_status= de la URL: el filtre s'aplica de debò i els filtres queden oberts", async () => {
    search = "q=fibra&match_status=candidate";
    respondWith(base([item("t1")]));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(matchCalls()[0]).toContain("q=fibra&match_status=candidate&sort=score&limit=25&offset=0");
    const details = container.querySelector("details")!;
    expect(details.open).toBe(true);
    expect(details.querySelector("summary")?.textContent).toContain("Filtres · 1 actiu");
    expect((screen.getByLabelText("Text") as HTMLInputElement).value).toBe("fibra");
    expect(screen.getByRole("button", { name: /Per revisar/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("sense filtres el formulari queda plegat; aplicar i esborrar passa pel mateix formulari", async () => {
    respondWith(base([item("t1")]));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toContain("Filtres");
    fireEvent.change(screen.getByLabelText("CPV"), { target: { value: "45314320" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("cpv=45314320&sort=score&limit=25&offset=0");
    expect(details.querySelector("summary")?.textContent).toContain("Filtres · 1 actiu");
    fireEvent.click(screen.getByRole("button", { name: "Neteja" }));
    await flush();
    expect(lastQuery()).toBe("sort=score&limit=25&offset=0");
  });

  it("la fila sencera obre la fitxa amb el ratolí i amb Enter", async () => {
    respondWith(base([item("t1")]));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const row = container.querySelector("tbody tr")!;
    fireEvent.click(row);
    expect(push).toHaveBeenCalledWith("/licitacions/t1");
    push.mockClear();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/licitacions/t1");
  });

  it("buit en candidates sense CPV al perfil → B1 amb «Completa el perfil»", async () => {
    search = "match_status=candidate";
    respondWith(base([], 0, { "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0 } } }));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Encara no sabem què busqueu" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Completa el perfil/ }).getAttribute("href")).toBe("/empresa");
  });

  it("buit en candidates amb CPV → «Cap candidata ara mateix» i «Mira les N obertes» passa a «Totes»", async () => {
    search = "match_status=candidate";
    respondWith(base([], 0));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Cap candidata ara mateix" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Mira les 100 obertes/ }));
    await flush();
    expect(lastQuery()).toBe("only_open=true&sort=score&limit=25&offset=0");
    expect(screen.getByRole("button", { name: /Totes/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("buit per filtres → B4 amb «Esborra els filtres», que crida el clear existent", async () => {
    search = "q=inventat";
    respondWith(base([], 0));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: /Cap licitació encaixa amb el filtre/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Esborra els filtres" }));
    await flush();
    expect(lastQuery()).toBe("sort=score&limit=25&offset=0");
  });

  it("ingesta buida: la instrucció tècnica va al detall tècnic, no al títol", async () => {
    respondWith(base([], 0, { "/tenders/facets": { ...facets, total_tenders: 0 }, "/companies/c1/matches/summary": { ...summary, total_tenders: 0 } }));
    const { container } = render(<TendersFeedPage />);
    const heading = await screen.findByRole("heading", { name: "Encara no s'ha llegit cap licitació" });
    expect(heading.textContent).not.toContain("python");
    const details = [...container.querySelectorAll("details")].find((element) => element.textContent?.includes("app.ingestion.placsp"));
    expect(details).toBeTruthy();
  });

  it("si la llista falla mostra l'error E1 amb reintent", async () => {
    respondWith(base([], 0, { "/companies/c1/matches?": new Error("503") }));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar les oportunitats" })).toBeTruthy();
    const before = matchCalls().length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(matchCalls().length).toBeGreaterThan(before);
  });

  it("pre-fits desactualitzats → bàner d'atenció amb «Recalcula», que fa el POST de sempre", async () => {
    respondWith(base([item("t1")], 1, { "/companies/c1/matches/summary": { ...summary, stale: 7 } }));
    post.mockResolvedValue({ tenders_evaluated: 1, lots_evaluated: 0, created: 0, updated: 1, duration_ms: 10, rules_version: "v1" });
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("7 pre-fits desactualitzats");
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/matches/recalculate", {});
  });
});
