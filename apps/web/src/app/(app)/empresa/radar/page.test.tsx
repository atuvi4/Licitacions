import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { role: "owner", company: { id: "c1", legal_name: "Empresa Demo Telecom SL", trade_name: null } },
  }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import RadarPreferencesPage from "./page";

const BASE = "/companies/c1";

const cpvs = [
  { id: "p1", company_id: "c1", cpv_code: "45314320", description: "Instal·lació de cables", priority: 8, active: true, excluded: false, notes: null },
  { id: "p2", company_id: "c1", cpv_code: "32500000", description: "CPV 32500000", priority: 5, active: true, excluded: false, notes: null },
  { id: "p3", company_id: "c1", cpv_code: "45000000", description: "Obres", priority: 1, active: true, excluded: true, notes: null },
];
const territories = [
  { id: "t1", name: "Girona", territory_type: "province", code: "ES512", priority: 10, willing_to_work: true, notes: null, created_at: "", updated_at: "", created_by: null, updated_by: null },
  { id: "t2", name: "Lleida", territory_type: "province", code: null, priority: 2, willing_to_work: false, notes: null, created_at: "", updated_at: "", created_by: null, updated_by: null },
];
const preferences = {
  id: "cp1",
  created_at: null,
  updated_at: null,
  created_by: null,
  updated_by: null,
  company_id: "c1",
  minimum_interesting_contract: "50000",
  preferred_contract_min: null,
  preferred_contract_max: "400000",
  maximum_recommended_contract: null,
  maximum_assumable_contract: null,
  currency: "EUR",
  notes: null,
};

function respond(overrides: Record<string, unknown> = {}) {
  const map: Record<string, unknown> = {
    [`${BASE}/cpv-preferences`]: cpvs,
    [`${BASE}/territories`]: territories,
    [`${BASE}/contract-preferences`]: preferences,
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    const value = map[path];
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Preferències del Radar (B4)", () => {
  beforeEach(() => {
    get.mockReset();
    respond();
  });

  it("mostra les tres preferències que el Radar fa servir, separades del coneixement, amb la seva pantalla d'edició", async () => {
    const { container } = render(<RadarPreferencesPage />);
    await screen.findByRole("heading", { level: 1, name: "Preferències del Radar" });
    await flush();
    expect(container.textContent).toContain("Empresa Demo Telecom SL");
    expect(container.textContent).toMatch(/El Radar només fa servir aquestes preferències/);
    expect(container.textContent).not.toMatch(/completitud|%|Completa el perfil/);

    const cpv = screen.getByRole("heading", { level: 2, name: "Codis CPV" }).closest("section")!;
    expect(cpv.textContent).toContain("2 actius · 1 exclòs");
    expect(cpv.textContent).toContain("45314320");
    expect(cpv.textContent).toContain("Instal·lació de cables");
    // Un codi sense descripció al catàleg no es repeteix a si mateix (D4).
    expect(cpv.textContent).not.toContain("CPV 32500000");
    expect(cpv.textContent).toContain("Exclòs");
    expect(cpv.querySelector("a[href='/empresa/cpv']")).not.toBeNull();

    const territory = screen.getByRole("heading", { level: 2, name: "Territoris" }).closest("section")!;
    expect(territory.textContent).toContain("Girona");
    expect(territory.textContent).toContain("ES512");
    expect(territory.textContent).toContain("Lleida");
    expect(territory.textContent).toContain("Descartat");
    expect(territory.querySelector("a[href='/empresa/territoris']")).not.toBeNull();

    const amounts = screen.getByRole("heading", { level: 2, name: "Franja d'import" }).closest("section")!;
    expect(amounts.textContent).toContain("50.000");
    expect(amounts.textContent).toContain("400.000");
    expect(amounts.textContent).toContain("Sense especificar");
    expect(amounts.querySelector("a[href='/empresa/solvencia']")).not.toBeNull();
  });

  it("sense cap preferència ho diu, sense judici, amb la sortida a cada pantalla", async () => {
    respond({ [`${BASE}/cpv-preferences`]: [], [`${BASE}/territories`]: [], [`${BASE}/contract-preferences`]: { ...preferences, minimum_interesting_contract: null, preferred_contract_max: null } });
    const { container } = render(<RadarPreferencesPage />);
    await screen.findByRole("heading", { level: 1, name: "Preferències del Radar" });
    await flush();
    expect(container.textContent).toContain("Cap codi CPV");
    expect(container.textContent).toContain("Cap territori");
    expect(container.textContent).toContain("Cap franja d'import");
    expect(container.textContent).toContain("el Radar no pot prioritzar");
    expect(screen.getByRole("link", { name: "Tria codis CPV" }).getAttribute("href")).toBe("/empresa/cpv");
    expect(screen.getByRole("link", { name: "Tria territoris" }).getAttribute("href")).toBe("/empresa/territoris");
    expect(screen.getByRole("link", { name: "Posa la franja d'import" }).getAttribute("href")).toBe("/empresa/solvencia");
  });

  it("si una crida falla, la resta es pinta i l'error es diu amb reintent", async () => {
    respond({ [`${BASE}/territories`]: new Error("503") });
    const { container } = render(<RadarPreferencesPage />);
    await screen.findByRole("heading", { level: 1, name: "Preferències del Radar" });
    await flush();
    expect(container.textContent).toContain("45314320");
    expect(container.textContent).toContain("No s'ha pogut carregar: 503");
    expect(screen.getAllByRole("button", { name: /Torna-ho a provar/ }).length).toBeGreaterThan(0);
  });
});
