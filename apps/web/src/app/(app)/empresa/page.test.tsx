import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CompletenessSection, DashboardSummary } from "@/lib/types";

const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { role: "owner", company: { id: "c1", legal_name: "Empresa Demo Telecom SL", trade_name: null } },
  }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import ProfileHubPage from "./page";

type Deferred = { resolve: (value: unknown) => void; reject: (error: unknown) => void };

function section(key: string, label: string, status: CompletenessSection["status"], detail: string): CompletenessSection {
  return { key, label, weight: 10, ratio: 1, points: 10, status, detail, requires_evidence: false };
}

const SECTIONS: CompletenessSection[] = [
  section("general", "Dades generals", "complete", "15 de 15 camps"),
  section("services", "Serveis", "complete", "3 serveis actius"),
  section("territories", "Àmbit geogràfic", "complete", "4 territoris"),
  section("cpv", "CPV", "complete", "9 codis actius"),
  section("solvency", "Solvència econòmica", "complete", "3 exercicis"),
  section("certifications", "Certificacions", "complete", "2 certificacions, 2 amb evidència"),
  section("registrations", "Registres i classificacions", "empty", "cap registre"),
  section("insurances", "Assegurances", "empty", "cap pòlissa"),
  section("team", "Equip humà", "complete", "4 persones"),
  section("experience", "Experiència", "partial", "5 projectes, 2 sense import"),
  section("documents", "Documents", "partial", "6 documents, 2 sense vincle"),
];

const summary: DashboardSummary = {
  company: { id: "c1", legal_name: "Empresa Demo Telecom SL", trade_name: null },
  completeness: { score: 78, sections: SECTIONS },
  counts: { services: 3, territories: 4, financial_years: 3, certifications: 2, registrations: 0, insurances: 0, people: 4, qualifications: 3, cpv_preferences: 9, contract_preferences: 0, projects: 5, documents: 6, document_links: 4 },
  expiry_alerts: [
    { entity_type: "company_certification", entity_id: "cert-1", label: "ISO 9001", expires_at: "2026-10-16", days_left: 30, temporal_status: "expiring_soon" },
    { entity_type: "company_insurance", entity_id: "ins-1", label: "Pòlissa RC 2026", expires_at: "2026-09-02", days_left: -14, temporal_status: "expired" },
    { entity_type: "document", entity_id: "doc-1", label: "Certificat AEAT.pdf", expires_at: "2026-09-19", days_left: 3, temporal_status: "expiring_soon" },
  ],
};

const company = { id: "c1", legal_name: "Empresa Demo Telecom SL", trade_name: null, updated_at: "2026-09-13T10:00:00Z", created_at: "2026-01-01T00:00:00Z", created_by: null, updated_by: null };

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

describe("El nostre perfil (hub)", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("mentre carrega pinta l'esquelet amb el títol real i cap percentatge", async () => {
    const pending: Deferred = { resolve: () => undefined, reject: () => undefined };
    respondWith({ "/companies/c1/summary": pending, "/companies/c1": company });
    const { container } = render(<ProfileHubPage />);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "El nostre perfil" })).toBeTruthy();
    expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/%/);
  });

  it("agrupa les nou àrees de coneixement per estat real; les preferències del Radar van a part i cap percentatge (B1/B4)", async () => {
    respondWith({ "/companies/c1/summary": summary, "/companies/c1": company });
    const { container } = render(<ProfileHubPage />);
    expect(await screen.findByText("Registres i classificacions")).toBeTruthy();

    // Capçalera: empresa · N àrees de coneixement · última modificació (de GET /companies/{id}).
    expect(screen.getByText(/Empresa Demo Telecom SL · 9 àrees · última modificació/)).toBeTruthy();

    // C4 (MUST 4): cap bàner ni acció principal que cridi a completar les àrees buides.
    expect(container.textContent).not.toMatch(/Comença per/);
    expect(screen.queryByRole("status")).toBeNull();
    expect(container.querySelector("a[data-variant='primary']")).toBeNull();

    // Grups (F12): «Sense dada» (to neutre, mai vermell) i «Amb dades»; files = les 9 àrees de coneixement.
    const table = container.querySelector("table")!;
    expect(table.querySelectorAll("tbody tr[data-tint]").length).toBe(0);
    expect(table.querySelectorAll("tbody .text-danger").length).toBe(0);
    expect(table.textContent).toContain("Sense dada");
    expect(table.querySelectorAll("tbody p.font-medium").length).toBe(SECTIONS.length - 2);
    expect(container.textContent).not.toMatch(/Amb dada incompleta|Al dia/);
    expect(table.querySelector("a[href='/empresa/cpv']")).toBeNull();
    expect(table.querySelector("a[href='/empresa/territoris']")).toBeNull();

    // Acció: cap primària (no és una llista de deures); cada àrea porta a la seva pantalla.
    const actions = [...table.querySelectorAll("a[href]")];
    expect(actions.some((a) => a.getAttribute("href") === "/empresa/registres")).toBe(true);
    expect(actions.some((a) => a.getAttribute("href") === "/empresa/dades")).toBe(true);
    expect(actions.some((a) => a.getAttribute("href") === "/experiencia")).toBe(true);
    expect(container.textContent).not.toMatch(/Completa-ho/);

    // «Què en depèn» és text genèric: cap recompte (C-5).
    const depends = [...table.querySelectorAll("[data-depends]")];
    expect(depends.length).toBe(SECTIONS.length - 2);
    for (const cell of depends) expect(cell.textContent).not.toMatch(/\d/);
    expect(container.textContent).not.toContain("Què desbloquejaries");

    // Cap mesurador de completitud enlloc (B1): el peu només compta àrees, de manera neutra (F12).
    expect(screen.getByText("7 àrees amb dades · 2 sense dada")).toBeTruthy();
    expect(container.textContent).not.toMatch(/demanen atenció/);
    expect(container.textContent).not.toMatch(/%/);
    expect(container.textContent).not.toMatch(/completitud/i);

    // Les preferències del Radar, en una targeta pròpia amb el seu estat real i la seva pantalla (B4).
    const radar = screen.getByRole("heading", { name: "Preferències del Radar" }).closest("section")!;
    expect(radar.textContent).toContain("CPV");
    expect(radar.textContent).toContain("9 codis actius");
    expect(radar.textContent).toContain("Àmbit geogràfic");
    expect(radar.textContent).toContain("4 territoris");
    expect(radar.querySelector("a[href='/empresa/radar']")).not.toBeNull();

    // Caducitats amb StatusPill de TEMPORAL_LEVEL, dies reals i enllaç a l'àrea.
    const expiries = screen.getByText("Caducitats que t'afecten").closest("section")!;
    const pills = [...expiries.querySelectorAll("[data-level]")];
    // Ordenades per urgència: la caducada primer.
    expect(pills.map((pill) => pill.getAttribute("data-level"))).toEqual(["blocking", "attention", "attention"]);
    expect(expiries.textContent).toContain("en 30 dies");
    expect(expiries.textContent).toContain("fa 14 dies");
    expect(expiries.querySelector("a[href='/empresa/assegurances']")).not.toBeNull();
  });

  it("F12/C4: els textos del servidor (ja neutres i amb accents a l'origen) es mostren tal qual; cap «dada incompleta» ni deure", async () => {
    // Els textos exactes de `services/completeness.py` (test_dashboard ho fixa al backend).
    const serverSections: CompletenessSection[] = [
      { ...section("general", "Dades generals", "complete", "12 de 12 camps informats") },
      { ...section("services", "Serveis", "partial", "1 servei actiu"), ratio: 0.33 },
      { ...section("territories", "Àmbit geogràfic", "partial", "1 territori on es vol treballar"), ratio: 0.5 },
      { ...section("cpv", "CPV", "partial", "2 CPV d'interès actius"), ratio: 0.66 },
      { ...section("certifications", "Certificacions", "partial", "2 certificacions · 1 amb evidència documental · 1 declarada"), ratio: 0.75, requires_evidence: true },
      { ...section("registrations", "Registres i classificacions", "partial", "1 registre · declarat"), ratio: 0.5, requires_evidence: true },
      { ...section("experience", "Experiència", "partial", "3 projectes · 2 amb evidència documental · 1 declarat"), ratio: 0.83, requires_evidence: true },
    ];
    respondWith({
      "/companies/c1/summary": { ...summary, completeness: { score: 61, sections: serverSections }, expiry_alerts: [] },
      "/companies/c1": company,
    });
    const { container } = render(<ProfileHubPage />);
    expect(await screen.findByText("Registres i classificacions")).toBeTruthy();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/Amb dada incompleta|demanen atenció|demana atenció|Afegeix-hi dades/);
    expect(text).not.toMatch(/sense evidencia|sense evidència|certificacio\(s\)|evidencia\b|interes\b|recomanats/);
    expect(text).toContain("2 certificacions · 1 amb evidència documental · 1 declarada");
    expect(text).toContain("1 registre · declarat");
    expect(text).toContain("3 projectes · 2 amb evidència documental · 1 declarat");
    expect(text).toContain("2 CPV d'interès actius");
    // L'estat d'una àrea amb dades és informació: l'acció és «Revisa», mai un deure.
    const table = container.querySelector("table")!;
    const registrations = [...table.querySelectorAll("a[href='/empresa/registres']")];
    expect(registrations.map((a) => a.textContent)).toEqual(["Revisa"]);
    expect(screen.getByText("Les 5 àrees tenen dades")).toBeTruthy();
    // El Radar: dades definides, sense «Parcial» per tenir menys codis dels «recomanats».
    const radar = screen.getByRole("heading", { name: "Preferències del Radar" }).closest("section")!;
    expect(radar.textContent).not.toContain("Parcial");
  });

  it("un perfil a zero no té cap benvinguda d'onboarding: les mateixes àrees, el bàner i el Radar a part (B1)", async () => {
    respondWith({
      "/companies/c1/summary": { ...summary, completeness: { score: 0, sections: SECTIONS.map((item) => ({ ...item, status: "empty" as const, detail: "cap dada" })) }, expiry_alerts: [] },
      "/companies/c1": company,
    });
    const { container } = render(<ProfileHubPage />);
    expect(await screen.findByText("Registres i classificacions")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Tres coses del perfil/ })).toBeNull();
    expect(container.textContent).not.toMatch(/Per començar|uns 10 minuts|3 passos|%/);
    // C4: les àrees buides surten al grup «Sense dada», en to neutre, sense bàner que cridi a completar.
    expect(screen.queryByRole("status")).toBeNull();
    expect(container.textContent).not.toMatch(/Comença per/);
    expect(container.querySelector("table")!.textContent).toContain("Sense dada");
    expect(container.querySelectorAll("tbody tr[data-tint]").length).toBe(0);
    const radar = screen.getByRole("heading", { name: "Preferències del Radar" }).closest("section")!;
    expect(radar.textContent).toMatch(/cap dada/i);
    expect(radar.textContent).toContain("Sense dada");
    expect(radar.querySelector("a[href='/empresa/radar']")).not.toBeNull();
  });

  it("si el resum falla mostra l'error amb reintent; si només falla l'empresa, la pàgina es pinta igualment", async () => {
    respondWith({ "/companies/c1/summary": new Error("503"), "/companies/c1": company });
    render(<ProfileHubPage />);
    expect(await screen.findByRole("heading", { name: "No hem pogut carregar el perfil" })).toBeTruthy();
    const calls = get.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    expect(get.mock.calls.length).toBeGreaterThan(calls);
  });

  it("sense l'empresa (GET /companies/{id} falla) la capçalera omet la data i res més canvia", async () => {
    respondWith({ "/companies/c1/summary": summary, "/companies/c1": new Error("500") });
    render(<ProfileHubPage />);
    expect(await screen.findByText("Empresa Demo Telecom SL · 9 àrees")).toBeTruthy();
    expect(screen.queryByText(/última modificació/)).toBeNull();
  });
});
