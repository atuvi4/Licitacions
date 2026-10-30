import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const summary: MatchSummary = { total_tenders: 148, evaluated_count: 88, actionable: 100, candidates: 12, high_fit: 5, low_fit: 30, excluded: 4, closed: 40, insufficient_data: 2, stale: 0, rules_version: "v1", last_calculated_at: "2026-09-15T00:00:00Z" };
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
    "/companies/c1/tenders/facets": facets,
    "/companies/c1/summary": profile,
    ...extra,
  };
}

// Dates fixes a les licitacions de prova (termini el 30-10-2026 a les 12:00Z): es congela només
// `Date`, abans del termini, perquè «En termini» no depengui del dia que es passen les proves.
const NOW = new Date("2026-10-21T12:00:00Z");

beforeEach(() => {
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Oportunitats · Per revisar (la vista del pre-fit, intacta)", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    push.mockReset();
    search = "vista=revisar";
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
    expect(lastQuery()).toBe("match_status=candidate&sort=score&limit=25&offset=0");

    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent?.trim());
    expect(headers).toEqual(HEADERS);

    // Vistes: «Totes amb pre-fit» ja no hi és; «Prioritzades» (candidate, D2) és la vista per defecte, sempre premuda.
    expect(screen.queryByRole("button", { name: /Totes/ })).toBeNull();
    expect(container.textContent).not.toContain("Totes amb pre-fit");
    expect(container.textContent).not.toContain("Per revisar");
    const views = screen.getAllByRole("button", { pressed: true });
    expect(views).toHaveLength(1);
    expect(views[0].textContent).toContain("Prioritzades");
    expect(views[0].textContent).toContain("12");
    expect(views[0].querySelector("[data-level='action'][data-outline='true']")).not.toBeNull();

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
    expect(rows[1].textContent).toContain("No consta a les dades de la font");
    expect(rows[0].textContent).not.toMatch(/probabilitat/i);
    expect(container.textContent).toContain("no és una probabilitat de guanyar");

    expect(screen.getByText("148 licitacions · pàgina 1 de 6")).toBeTruthy();
    expect(container.textContent).toContain("v1");
    expect(container.textContent).toContain("Font: PLACSP · perfils de contractant");
    // El select d'ordenació té etiqueta.
    expect(screen.getByLabelText("Ordena per")).toBeTruthy();
  });

  it("canviar de vista, d'ordre i de pàgina genera les mateixes queries que abans", async () => {
    respondWith(base([item("t1")], 60));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.click(screen.getByRole("button", { name: /Prioritzades/ }));
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
    expect(screen.getByRole("button", { name: /Prioritzades/ }).getAttribute("aria-pressed")).toBe("true");
    // Xips dels filtres actius (C5): treure'n un torna a consultar sense ell.
    fireEvent.click(screen.getByRole("button", { name: "Treu el filtre Text: «fibra»" }));
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=score&limit=25&offset=0");
    expect(screen.queryByRole("list", { name: "Filtres actius" })).toBeNull();
  });

  it("amb un filtre de CPV la fila diu quin CPV ha coincidit quan no és el principal (C4, feed)", async () => {
    search = "match_status=candidate";
    respondWith(
      base([
        item("t1", {
          cpvs: [
            { cpv_code: "45314320", is_main: true, description: "Instal·lació de cables" },
            { cpv_code: "32000000", is_main: false, description: "Equip de ràdio" },
          ],
        }),
      ]),
    );
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(container.querySelector("[data-cpv-match]")).toBeNull();
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "32" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("cpv=32&match_status=candidate&sort=score&limit=25&offset=0");
    const row = container.querySelector("tbody tr")!;
    expect(row.querySelector("[data-cpv-match]")!.textContent).toContain("Coincideix amb el filtre per un altre CPV de la llista, 32000000 · Equip de ràdio");
  });

  it("sense filtres el formulari queda plegat; aplicar i esborrar passa pel mateix formulari", async () => {
    respondWith(base([item("t1")]));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const details = container.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toContain("Filtres");
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "45314320" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("cpv=45314320&match_status=candidate&sort=score&limit=25&offset=0");
    expect(details.querySelector("summary")?.textContent).toContain("Filtres · 1 actiu");
    fireEvent.click(screen.getByRole("button", { name: "Neteja" }));
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=score&limit=25&offset=0");
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

  it("buit en candidates amb el perfil realment buit (sense CPV, territoris ni preferències) → B1 amb la sortida cap a les preferències del Radar", async () => {
    search = "match_status=candidate";
    respondWith(base([], 0, {
      "/companies/c1/matches/summary": { ...summary, candidates: 0 },
      "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0, territories: 0, contract_preferences: 0 } },
    }));
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Encara no sabem què busqueu" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Defineix les preferències del Radar/ }).getAttribute("href")).toBe("/empresa/radar");
    expect(container.textContent).not.toMatch(/Completa el perfil|3 passos/);
  });

  it("regles v3: territoris sí, CPV no, avaluades > 0 i 0 candidates → culpa el perfil (mateixa causa que Avui)", async () => {
    // Abans («retoc») no es culpava el perfil: sense CPV encara es podia ser candidata. Amb el
    // mínim de CPV de les regles v3, sense cap CPV al perfil no n'hi pot haver cap.
    search = "match_status=candidate";
    respondWith(base([], 0, {
      "/companies/c1/matches/summary": { ...summary, evaluated_count: 88, candidates: 0, insufficient_data: 2 },
      "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0, territories: 2, contract_preferences: 0 } },
    }));
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Sense CPV al perfil, cap licitació no pot ser candidata" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Cap candidata ara mateix" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Encara no sabem què busqueu" })).toBeNull();
    // Les licitacions SÍ que estan avaluades: no és el text del perfil buit («només llistar-les»).
    expect(container.textContent).toContain("El Radar ja ha avaluat 88 licitacions, però una licitació només és candidata");
    expect(container.textContent).not.toMatch(/només llistar-les/);
    expect(screen.getByRole("link", { name: /Afegeix els CPV al perfil/ }).getAttribute("href")).toBe("/empresa/radar");
  });

  it("F1: Radar amb el perfil complet però cap licitació avaluada → «El Radar encara no s'ha calculat» + càlcul, mai «ampliar»", async () => {
    search = "match_status=candidate";
    const zero = { ...summary, evaluated_count: 0, actionable: 0, candidates: 0, high_fit: 0, low_fit: 0, excluded: 0, closed: 0, insufficient_data: 0, total_tenders: 46 };
    respondWith(base([], 0, { "/companies/c1/matches/summary": zero }));
    post.mockResolvedValue({ tenders_evaluated: 46, lots_evaluated: 0, created: 46, updated: 0, duration_ms: 10, rules_version: "v1" });
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "El Radar encara no s'ha calculat" })).toBeTruthy();
    expect(container.textContent).toContain("El pre-fit encara no s'ha calculat per a cap de les 46 licitacions");
    expect(container.textContent).not.toMatch(/Cap candidata|ampliar|encaixa prou|Encara no sabem què busqueu/);
    // Els comptadors de les vistes no pinten zeros que semblin resultats.
    const views = screen.getByRole("group", { name: "Vistes" });
    expect(within(views).getByRole("button", { name: /Prioritzades/ }).textContent).toBe("Prioritzades");
    fireEvent.click(screen.getByRole("button", { name: "Calcula el pre-fit" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/matches/recalculate", {});
  });

  it("F1: amb el perfil buit però cap avaluació, tampoc culpa el perfil: diu que no s'ha calculat", async () => {
    search = "match_status=candidate";
    respondWith(base([], 0, {
      "/companies/c1/matches/summary": { ...summary, evaluated_count: 0, candidates: 0 },
      "/companies/c1/summary": { ...profile, counts: { ...profile.counts, cpv_preferences: 0 } },
    }));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "El Radar encara no s'ha calculat" })).toBeTruthy();
  });

  it("buit en candidates amb CPV → «Cap candidata ara mateix» i «Mira les N obertes» passa a «Totes»", async () => {
    search = "match_status=candidate";
    respondWith(base([], 0));
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Cap candidata ara mateix" })).toBeTruthy();
    // Avaluades i cap candidata: es diu entre quantes, i no es culpa el perfil (F1).
    expect(container.textContent).toContain("Cap de les 88 licitacions avaluades encaixa prou amb el perfil declarat");
    expect(container.textContent).not.toMatch(/ampliar/);
    fireEvent.click(screen.getByRole("button", { name: /Mira les 100 obertes/ }));
    await flush();
    expect(lastQuery()).toBe("only_open=true&sort=score&limit=25&offset=0");
    expect(screen.queryByRole("button", { name: /Totes/ })).toBeNull();
  });

  it("buit per filtres → B4 amb «Esborra els filtres», que crida el clear existent", async () => {
    search = "vista=revisar&q=inventat";
    respondWith(base([], 0));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: /Cap licitació encaixa amb el filtre/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Esborra els filtres" }));
    await flush();
    expect(lastQuery()).toBe("match_status=candidate&sort=score&limit=25&offset=0");
  });

  it("cap text promet un pre-fit calculat per a totes les licitacions (3B-2 §7.1.4)", async () => {
    respondWith(base([], 0, { "/companies/c1/tenders/facets": { ...facets, total_tenders: 0 }, "/companies/c1/matches/summary": { ...summary, total_tenders: 0 } }));
    const { container } = render(<TendersFeedPage />);
    await screen.findByRole("heading", { name: "Encara no s'ha llegit cap licitació" });
    expect(container.textContent).not.toMatch(/per a totes|El pre-fit no n'amaga cap|apareixeran aquí amb el pre-fit calculat/);
    expect(container.textContent).toContain("CPV, territoris o preferències de contracte");
  });

  it("ingesta buida: la instrucció tècnica va al detall tècnic, no al títol", async () => {
    respondWith(base([], 0, { "/companies/c1/tenders/facets": { ...facets, total_tenders: 0 }, "/companies/c1/matches/summary": { ...summary, total_tenders: 0 } }));
    const { container } = render(<TendersFeedPage />);
    const heading = await screen.findByRole("heading", { name: "Encara no s'ha llegit cap licitació" });
    expect(heading.textContent).not.toContain("python");
    expect(heading.textContent).not.toContain("python");
    const details = [...container.querySelectorAll("details")].find((element) => element.textContent?.includes("app.ingestion.sync"));
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

  it("el peu etiqueta la font en català i no mostra mai el codi cru ni estats desconeguts", async () => {
    respondWith(base([item("t1", { source_code: "placsp_agregadas", status: "nou_estat" as never, procedure_type: "nou_proc" as never })], 1));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(container.textContent).toContain("Font: PLACSP · plataformes agregades (PSCP)");
    expect(container.textContent).not.toContain("placsp_agregadas");
    expect(container.textContent).not.toMatch(/nou_estat|nou_proc/);
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

const catalogueFacets: TenderFacets = {
  ...facets,
  sources: [
    { code: "PLACSP", name: "PLACSP", count: 100 },
    { code: "placsp_agregadas", name: "PLACSP · plataformes agregades", count: 48 },
  ],
  status_counts: { open: 120, cancelled: 3 },
};
const FORBIDDEN = /bloqueig|exclosa|no apta|retirada/i;
const catalogueRows = () => document.querySelectorAll("article[data-catalogue-row]");

describe("Oportunitats · Totes (catàleg complet)", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    push.mockReset();
    search = "";
  });

  it("per defecte obre «Totes» amb la consulta neutral i mai porta min_score ni match_status", async () => {
    respondWith(base([item("t1")], 148, { "/companies/c1/tenders/facets": catalogueFacets }));
    render(<TendersFeedPage />);
    expect(await screen.findByText("Licitació t1")).toBeTruthy();
    expect(lastQuery()).toBe("mode=all&sort=published&limit=25&offset=0");
    for (const call of matchCalls()) {
      expect(call).not.toContain("min_score");
      expect(call).not.toContain("match_status");
    }
    expect(screen.getByRole("tab", { name: "Totes" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Radar · prioritzades" }).getAttribute("aria-selected")).toBe("false");
    expect(screen.getByLabelText("Ordena per")).toBeTruthy();
    // El pre-fit no canvia l'ordre per defecte: només hi ha ordres explícits de l'usuari.
    expect(screen.getByText("148 licitacions · pàgina 1 de 6")).toBeTruthy();
  });

  it("?q= obre «Totes» amb el text aplicat, i el text arriba a la consulta", async () => {
    search = "q=fibra";
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(matchCalls()[0]).toBe("/companies/c1/matches?mode=all&q=fibra&sort=published&limit=25&offset=0");
    expect((screen.getByLabelText("Cerca al catàleg") as HTMLInputElement).value).toBe("fibra");
    expect(screen.getByRole("tab", { name: "Totes" }).getAttribute("aria-selected")).toBe("true");
  });

  it("?match_status= (d'Avui) obre «Radar · prioritzades»", async () => {
    search = "match_status=candidate";
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(screen.getByRole("tab", { name: "Radar · prioritzades" }).getAttribute("aria-selected")).toBe("true");
    expect(lastQuery()).toContain("match_status=candidate");
  });

  it("la pestanya «Radar · prioritzades» s'obre per la URL sense perdre el que hi ha", async () => {
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.click(screen.getByRole("tab", { name: "Radar · prioritzades" }));
    expect(push).toHaveBeenCalledWith("/licitacions?vista=revisar", { scroll: false });
  });

  it("xips dels filtres actius amb «×» (C5): treure'n un deixa la resta i torna a consultar", async () => {
    search = "q=fibra";
    respondWith(base([item("t1")], 1, { "/companies/c1/tenders/facets": catalogueFacets }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^Territori \(NUTS\)/), { target: { value: "ES51" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&q=fibra&nuts=ES51&sort=published&limit=25&offset=0");
    const chips = screen.getByRole("list", { name: "Filtres actius" });
    expect(chips.textContent).toContain("Text: «fibra»");
    expect(chips.textContent).toContain("NUTS: ES51");
    fireEvent.click(screen.getByRole("button", { name: "Treu el filtre Text: «fibra»" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&nuts=ES51&sort=published&limit=25&offset=0");
    expect((screen.getByLabelText("Cerca al catàleg") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("list", { name: "Filtres actius" }).textContent).not.toContain("Text:");
  });

  it("F2: el filtre «En termini» demana l'estat accionable (`in_term`), no l'estat cru `open`", async () => {
    respondWith(base([item("t1")], 1, { "/companies/c1/tenders/facets": catalogueFacets }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const select = screen.getByLabelText(/^Estat de la licitació/) as HTMLSelectElement;
    const labels = [...select.options].map((option) => option.textContent);
    expect(labels).toContain("En termini");
    expect(labels).toContain("Publicada (estat de la font)");
    expect(labels.filter((label) => label === "En termini")).toHaveLength(1);
    fireEvent.change(select, { target: { value: "in_term" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&in_term=true&sort=published&limit=25&offset=0");
    expect(screen.getByRole("list", { name: "Filtres actius" }).textContent).toContain("Estat: En termini");
  });

  it("C3: «termini obert» i «En termini» conviuen sense contradir-se: l'ajuda diu la diferència", async () => {
    search = "vista=revisar";
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const checkbox = screen.getByRole("checkbox", { name: "Només amb termini obert (qualsevol estat de la font)" });
    expect(checkbox).toBeTruthy();
    const help = screen.getByText(/Inclou les licitacions amb termini posterior a ara/);
    expect(help.textContent).toContain("«En termini»");
    expect(screen.getByLabelText(/^Estat de la licitació/).closest("label")!.textContent).toContain(
      "publicada i amb el termini encara obert",
    );
  });

  it("F16: una paraula al CPV sense triar cap codi no s'aplica en silenci: avís visible, cap xip, cap `cpv=`", async () => {
    respondWith(base([item("t1")], 46, {
      "/companies/c1/tenders/facets": catalogueFacets,
      "/cpv-codes": [{ code: "32562000", description: "Cables de fibra òptica" }],
    }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "fibra" } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    // Escriure «fibra» mostra opcions (nom + codi).
    expect(screen.getByRole("button", { name: /32562000.*Cables de fibra òptica/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("El filtre de CPV no s'ha aplicat");
    expect(alert.textContent).toContain("«fibra»");
    expect(lastQuery()).not.toContain("cpv=");
    expect(screen.queryByRole("list", { name: "Filtres actius" })?.textContent ?? "").not.toContain("CPV");
    // Triar l'opció aplica el codi amb xip visible i treu l'avís.
    fireEvent.click(screen.getByRole("button", { name: /32562000/ }));
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toContain("cpv=32562000");
    expect(screen.getByRole("list", { name: "Filtres actius" }).textContent).toContain("CPV: 32562000");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("F16: a la vista del Radar el CPV també és el cercador: una paraula no es converteix en xip ni en `cpv=fibra`", async () => {
    search = "vista=revisar";
    respondWith(base([item("t1")], 1, { "/cpv-codes": [{ code: "32562000", description: "Cables de fibra òptica" }] }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "fibra" } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    expect(screen.getByRole("button", { name: /32562000/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("El filtre de CPV no s'ha aplicat");
    expect(lastQuery()).not.toContain("cpv=");
    expect(document.body.textContent).not.toContain("CPV: fibra");
  });

  it("F2: a la vista del Radar, el filtre «En termini» també viatja com a `in_term=true`", async () => {
    search = "vista=revisar";
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^Estat de la licitació/), { target: { value: "in_term" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("in_term=true&match_status=candidate&sort=score&limit=25&offset=0");
  });

  it("amb text, «Ordena per rellevància» només canvia l'ordre i ho diu: cap resultat amagat (C6)", async () => {
    search = "q=fibra";
    respondWith(base([item("t1")], 1));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.click(screen.getByRole("button", { name: "Ordena per rellevància" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&q=fibra&sort=relevance&limit=25&offset=0");
    expect(screen.queryByRole("button", { name: "Ordena per rellevància" })).toBeNull();
    expect(container.textContent).toContain("només canvia l'ordre, no amaga cap licitació");
    expect((screen.getByLabelText("Ordena per") as HTMLSelectElement).value).toBe("relevance");
  });

  it("sense text no s'ofereix cap ordre per rellevància", async () => {
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(screen.queryByRole("button", { name: "Ordena per rellevància" })).toBeNull();
  });

  it("el lloc d'execució sense resultats explica la cobertura parcial i porta al filtre per NUTS (C1)", async () => {
    respondWith(base([], 0));
    render(<TendersFeedPage />);
    await screen.findByRole("heading", { name: "Cap licitació al catàleg" });
    fireEvent.change(screen.getByLabelText(/^Lloc d'execució \(cobertura parcial\)/), { target: { value: "L'Escala" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&place_q=L%27Escala&sort=published&limit=25&offset=0");
    expect(await screen.findByRole("heading", { name: "Cap licitació amb aquest lloc d'execució" })).toBeTruthy();
    expect(screen.getByText(/només indiquen el territori amb el codi NUTS/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Filtra per NUTS" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&sort=published&limit=25&offset=0");
    expect(document.activeElement).toBe(screen.getByLabelText(/^Territori \(NUTS\)/));
  });

  it("la cerca s'aplica sola al cap de 300 ms, i Enter l'aplica a l'instant (D1)", async () => {
    vi.useFakeTimers();
    try {
      respondWith(base([item("t1")], 1));
      render(<TendersFeedPage />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const before = matchCalls().length;
      fireEvent.change(screen.getByLabelText("Cerca al catàleg"), { target: { value: "fib" } });
      fireEvent.change(screen.getByLabelText("Cerca al catàleg"), { target: { value: "fibra" } });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
      });
      expect(matchCalls().length).toBe(before);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(150);
      });
      expect(lastQuery()).toBe("mode=all&q=fibra&sort=published&limit=25&offset=0");
      expect(matchCalls().length).toBe(before + 1);

      fireEvent.change(screen.getByLabelText("Cerca al catàleg"), { target: { value: "fibra òptica" } });
      fireEvent.submit(screen.getByRole("search"));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(lastQuery()).toBe("mode=all&q=fibra+%C3%B2ptica&sort=published&limit=25&offset=0");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(400);
      });
      // El temporitzador no repeteix la consulta que Enter ja ha fet.
      expect(matchCalls().length).toBe(before + 2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("cada fila: títol, organisme, termini, import, CPV principal, font, estat factual, accions", async () => {
    respondWith(
      base(
        [
          item("t1", {
            source_code: "placsp_agregadas",
            cpvs: [
              { cpv_code: "32000000", is_main: false, description: "Equip" },
              { cpv_code: "45314320", is_main: true, description: "Instal·lació de cables" },
            ],
          }),
          item("t2", { status: "cancelled", source_removed_at: "2026-09-20T08:00:00Z", estimated_value: null }, null),
        ],
        2,
        { "/companies/c1/tenders/facets": catalogueFacets },
      ),
    );
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const [first, second] = [...catalogueRows()] as HTMLElement[];
    expect(first.textContent).toContain("Ajuntament de Sabadell");
    expect(first.textContent).toContain("EXP-t1");
    expect(first.textContent).toContain("240.000");
    expect(first.textContent).toContain("45314320");
    expect(first.textContent).toContain("Instal·lació de cables");
    expect(first.textContent).not.toContain("32000000");
    expect(first.textContent).toContain("PLACSP · plataformes agregades");
    expect(first.textContent).toContain("En termini");
    expect(first.textContent).not.toContain("Anul·lada");
    expect(first.textContent).not.toContain("Ja no disponible a la font");
    expect(first.textContent).toContain("92");

    expect(second.textContent).toContain("Anul·lada");
    expect(second.textContent).toContain("Ja no disponible a la font");
    expect(second.textContent).toContain("No consta a les dades de la font");
    // Sense pre-fit: la fila no en diu res (A9); l'avís és un de sol, a la capçalera de la llista.
    expect(second.textContent).not.toContain("Sense calcular");
    expect(second.textContent).not.toContain("Pre-fit 0");
    const notices = screen.getAllByText(/El pre-fit no s'ha calculat per a 1 licitació/);
    expect(notices).toHaveLength(1);
    // F3: la franja no culpa el perfil (no se sap si és la causa); ofereix calcular-lo.
    const notice = notices[0].closest("[role='status']")!;
    expect(notice.textContent).not.toMatch(/CPV|territoris|perfil/);
    post.mockResolvedValue({ tenders_evaluated: 2, lots_evaluated: 0, created: 1, updated: 1, duration_ms: 10, rules_version: "v1" });
    const before = matchCalls().length;
    fireEvent.click(within(notice as HTMLElement).getByRole("button", { name: "Calcula el pre-fit" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/matches/recalculate", {});
    expect(matchCalls().length).toBeGreaterThan(before);

    // Una sola acció a la targeta (D3).
    expect(within(first).getByRole("link", { name: "Obre la fitxa" }).getAttribute("href")).toBe("/licitacions/t1");
    expect(within(first).queryByRole("link", { name: /Analitzar|^Veure$/ })).toBeNull();
  });

  it("amb un filtre de CPV la targeta diu quin CPV ha coincidit quan no és el principal (C4)", async () => {
    respondWith(
      base(
        [
          item("t1", {
            cpvs: [
              { cpv_code: "45314320", is_main: true, description: "Instal·lació de cables" },
              { cpv_code: "32000000", is_main: false, description: "Equip de ràdio" },
            ],
          }),
        ],
        1,
        { "/cpv-codes?": [] },
      ),
    );
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(container.querySelector("[data-cpv-match]")).toBeNull();
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "32" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&cpv=32&sort=published&limit=25&offset=0");
    const line = container.querySelector("[data-cpv-match]")!;
    expect(line.getAttribute("data-cpv-match")).toBe("secondary");
    expect(line.textContent).toContain("Coincideix amb el filtre per un altre CPV de la llista, 32000000 · Equip de ràdio");
  });

  it("amb un filtre d'import, avisa de quantes no es mostren per no tenir import conegut", async () => {
    respondWith(base([item("t1")], 1, { "/companies/c1/matches?": { ...page([item("t1")], 1), excluded_without_amount: 7 } }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^Import mínim/), { target: { value: "100000" } });
    // P2-06: el filtre diu sobre quina xifra s'aplica.
    expect(screen.getByText(/S'aplica al valor estimat del contracte \(VEC\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&min_amount=100000&sort=published&limit=25&offset=0");
    expect(screen.getByTestId("without-amount").textContent).toContain("7 sense import conegut no es mostren");
  });

  it("sense excluded_without_amount no diu res d'imports", async () => {
    respondWith(base([item("t1")], 1));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(screen.queryByTestId("without-amount")).toBeNull();
  });

  it("els filtres viatgen amb els noms del contracte i la data final és inclosa", async () => {
    respondWith(base([item("t1")], 1, { "/companies/c1/tenders/facets": catalogueFacets }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText("Cerca al catàleg"), { target: { value: "telecomunicació" } });
    fireEvent.change(screen.getByLabelText(/^Organisme/), { target: { value: "Sabadell" } });
    fireEvent.change(screen.getByLabelText(/^Territori/), { target: { value: "es51" } });
    fireEvent.change(screen.getByLabelText(/^Lloc d'execució/), { target: { value: "Vallès" } });
    fireEvent.change(screen.getByLabelText("Publicades des de"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Termini fins a"), { target: { value: "2026-12-31" } });
    fireEvent.change(screen.getByLabelText("Tipus de contracte"), { target: { value: "services" } });
    fireEvent.change(screen.getByLabelText(/^Estat de la licitació/), { target: { value: "open" } });
    fireEvent.change(screen.getByLabelText("Font"), { target: { value: "placsp_agregadas" } });
    fireEvent.change(screen.getByLabelText("Disponibilitat a la font"), { target: { value: "true" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    const query = new URLSearchParams(lastQuery());
    expect(query.get("mode")).toBe("all");
    expect(query.get("q")).toBe("telecomunicació");
    expect(query.get("buyer_q")).toBe("Sabadell");
    expect(query.get("nuts")).toBe("es51");
    expect(query.get("place_q")).toBe("Vallès");
    expect(query.get("published_from")).toBe("2026-01-01");
    expect(query.get("deadline_to")).toBe("2026-12-31T23:59:59");
    expect(query.get("contract_type")).toBe("services");
    expect(query.get("status")).toBe("open");
    expect(query.get("source")).toBe("placsp_agregadas");
    expect(query.get("source_removed")).toBe("true");
    expect(query.has("min_score")).toBe(false);
    expect(query.has("match_status")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Neteja" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&sort=published&limit=25&offset=0");
  });

  it("el cercador de CPV consulta /cpv-codes?q= i triar una opció fixa el codi", async () => {
    respondWith(
      base([item("t1")], 1, {
        "/cpv-codes?": [{ code: "32500000", description: "Equip de telecomunicacions" }],
      }),
    );
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "telecom" } });
    const option = await screen.findByRole("button", { name: /32500000/ });
    expect(get.mock.calls.map((call) => String(call[0]))).toContain("/cpv-codes?q=telecom&limit=8");
    fireEvent.click(option);
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&cpv=32500000&sort=published&limit=25&offset=0");
  });

  it("un prefix de dígits al CPV es fixa tal qual", async () => {
    respondWith(base([item("t1")], 1, { "/cpv-codes?": [] }));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    fireEvent.change(screen.getByLabelText(/^CPV/), { target: { value: "4531" } });
    fireEvent.click(screen.getByRole("button", { name: "Aplica els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&cpv=4531&sort=published&limit=25&offset=0");
  });

  it("el DOM no diu mai bloqueig, exclosa, no apta ni retirada, ni amb pre-fit descartat o sense", async () => {
    respondWith(
      base(
        [
          item("t1", { status: "cancelled", source_removed_at: "2026-09-20T08:00:00Z" }, { status: "excluded", score: 0, exclusion_kind: "business", exclusion_reason_code: "X" }),
          item("t2", {}, null),
          item("t3", {}, { status: "insufficient_data", score: 12 }),
        ],
        3,
        { "/companies/c1/tenders/facets": catalogueFacets },
      ),
    );
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(container.textContent).not.toMatch(FORBIDDEN);
  });

  it("390 px: les files són targetes (cap taula) i res no depèn d'un desplaçament lateral", async () => {
    respondWith(base([item("t1"), item("t2")], 2));
    const { container } = render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    expect(container.querySelector("table")).toBeNull();
    expect(catalogueRows()).toHaveLength(2);
    for (const row of catalogueRows()) {
      expect(row.className).toContain("flex-col");
      expect(row.className).toContain("min-w-0");
      expect(row.className).not.toContain("overflow-x");
    }
  });

  it("buit amb filtres → «Esborra els filtres»; ingesta buida → detall tècnic", async () => {
    search = "q=inventat";
    respondWith(base([], 0));
    const first = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: /Cap licitació encaixa amb la cerca/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Esborra els filtres" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&sort=published&limit=25&offset=0");
    first.unmount();

    search = "";
    respondWith(base([], 0, { "/companies/c1/tenders/facets": { ...facets, total_tenders: 0 } }));
    const { container } = render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "Encara no s'ha llegit cap licitació" })).toBeTruthy();
    expect(container.textContent).toContain("python -m app.ingestion.sync --source placsp_agregadas --max-pages 2");
  });

  it("paginar i ordenar conserven la consulta neutral; «Rellevància» només amb text", async () => {
    respondWith(base([item("t1")], 60));
    render(<TendersFeedPage />);
    await screen.findByText("Licitació t1");
    const sort = screen.getByLabelText("Ordena per") as HTMLSelectElement;
    expect([...sort.options].map((option) => option.value)).not.toContain("relevance");
    fireEvent.click(screen.getByRole("button", { name: "Següent" }));
    await flush();
    expect(lastQuery()).toBe("mode=all&sort=published&limit=25&offset=25");
    fireEvent.change(screen.getByLabelText("Ordena per"), { target: { value: "deadline" } });
    await flush();
    expect(lastQuery()).toBe("mode=all&sort=deadline&limit=25&offset=0");
  });

  it("si la llista falla mostra l'error amb reintent", async () => {
    respondWith(base([], 0, { "/companies/c1/matches?": new Error("503") }));
    render(<TendersFeedPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar el catàleg" })).toBeTruthy();
  });
});
