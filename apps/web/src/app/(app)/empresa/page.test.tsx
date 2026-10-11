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

    // Bàner d'atenció amb els noms de les àrees buides i acció cap a la primera.
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("2 àrees sense dada");
    expect(banner.textContent).toContain("Registres i classificacions");
    expect(banner.textContent).toContain("Assegurances");
    expect(banner.querySelector("a")?.getAttribute("href")).toBe("/empresa/registres");

    // Grups: «Al dia» plegat; en desplegar-lo, files = les 9 àrees de coneixement (sense CPV ni àmbit geogràfic).
    const table = container.querySelector("table")!;
    expect(table.querySelectorAll("tbody p.font-medium").length).toBe(4);
    expect(table.querySelectorAll("tbody tr[data-tint='blocking']").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: /Al dia/ }));
    expect(table.querySelectorAll("tbody p.font-medium").length).toBe(SECTIONS.length - 2);
    expect(table.querySelector("a[href='/empresa/cpv']")).toBeNull();
    expect(table.querySelector("a[href='/empresa/territoris']")).toBeNull();

    // Acció: primari només a la primera fila «Sense dada»; cada àrea porta a la seva pantalla.
    const actions = [...table.querySelectorAll("a[href]")];
    const registrations = actions.find((a) => a.getAttribute("href") === "/empresa/registres")!;
    expect(registrations.getAttribute("data-variant")).toBe("primary");
    const insurances = actions.find((a) => a.getAttribute("href") === "/empresa/assegurances")!;
    expect(insurances.getAttribute("data-variant")).not.toBe("primary");
    expect(actions.some((a) => a.getAttribute("href") === "/empresa/dades")).toBe(true);
    expect(actions.some((a) => a.getAttribute("href") === "/experiencia")).toBe(true);
    expect(container.textContent).not.toMatch(/Completa-ho/);

    // «Què en depèn» és text genèric: cap recompte (C-5).
    const depends = [...table.querySelectorAll("[data-depends]")];
    expect(depends.length).toBe(SECTIONS.length - 2);
    for (const cell of depends) expect(cell.textContent).not.toMatch(/\d/);
    expect(container.textContent).not.toContain("Què desbloquejaries");

    // Cap mesurador de completitud enlloc (B1): el peu només compta àrees.
    expect(screen.getByText("4 àrees demanen atenció · 5 al dia")).toBeTruthy();
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

  it("un perfil a zero no té cap benvinguda d'onboarding: les mateixes àrees, el bàner i el Radar a part (B1)", async () => {
    respondWith({
      "/companies/c1/summary": { ...summary, completeness: { score: 0, sections: SECTIONS.map((item) => ({ ...item, status: "empty" as const, detail: "cap dada" })) }, expiry_alerts: [] },
      "/companies/c1": company,
    });
    const { container } = render(<ProfileHubPage />);
    expect(await screen.findByText("Registres i classificacions")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /Tres coses del perfil/ })).toBeNull();
    expect(container.textContent).not.toMatch(/Per començar|uns 10 minuts|3 passos|%/);
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("9 àrees sense dada");
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
