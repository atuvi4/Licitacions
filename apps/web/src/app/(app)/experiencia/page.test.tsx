import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { claimFixture, emptySnapshotFixture } from "@/test/knowledge-fixture";

const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args), post: vi.fn(), delete: vi.fn() } }));
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", membership: { role: "owner" } }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import ExperiencePage from "./page";

const BASE = "/companies/c1";

function project(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    company_id: "c1",
    name,
    client_name: "Ajuntament Demo",
    client_type: "public",
    sector: null,
    description: null,
    amount: "120000",
    currency: "EUR",
    start_date: "2025-01-01",
    end_date: "2025-12-31",
    location: null,
    status: "completed",
    reference_contact: null,
    notes: null,
    services: [],
    cpvs: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  };
}

const projects = [project("p1", "Xarxa FTTH de l'Escala"), project("p2", "Centraleta de Palamós")];

/** La fotografia diu quina referència té evidència vinculada: p1 sí, p2 no. */
function snapshot() {
  return {
    ...emptySnapshotFixture(),
    execution_references: [
      { key: "p1", source: "current_verified", accreditation_pending: false, claims: [claimFixture({ record_id: "p1", source: "current_verified", has_evidence: true })], hints: [] },
      { key: "p2", source: "current_declared", accreditation_pending: true, claims: [claimFixture({ record_id: "p2", has_evidence: false })], hints: [] },
    ],
  };
}

function respond(overrides: Record<string, unknown> = {}) {
  const map: Record<string, unknown> = {
    [`${BASE}/projects`]: projects,
    [`${BASE}/services`]: [],
    [`${BASE}/knowledge/snapshot`]: snapshot(),
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    const value = map[path];
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Experiència (B7): històric de licitacions ≠ referències acreditables", () => {
  beforeEach(() => {
    get.mockReset();
    respond();
  });

  it("dues seccions amb títols diferents: les referències amb evidència i les declarades sense", async () => {
    const { container } = render(<ExperiencePage />);
    await screen.findByText("Xarxa FTTH de l'Escala");
    await flush();

    const accredited = screen.getByRole("heading", { name: "Referències acreditables" }).closest("section")!;
    expect(accredited.textContent).toContain("Xarxa FTTH de l'Escala");
    expect(accredited.textContent).not.toContain("Centraleta de Palamós");
    expect(accredited.textContent).toContain("amb evidència vinculada");
    expect(within(accredited).getByText("Verificat")).toBeTruthy();

    const declared = screen.getByRole("heading", { name: "Referències declarades sense evidència" }).closest("section")!;
    expect(declared.textContent).toContain("Centraleta de Palamós");
    expect(declared.textContent).not.toContain("Xarxa FTTH de l'Escala");

    // L'històric de licitacions no és cap referència: es diu i es remet a on viu.
    const historic = screen.getByRole("heading", { name: "Històric de licitacions" }).closest("section")!;
    expect(historic.textContent).toContain("no acredita res");
    expect(historic.querySelector("a[href='/empresa/coneixement?tab=historic']")).not.toBeNull();
    expect(container.textContent).not.toMatch(/current_verified|current_declared/);
  });

  it("si la fotografia no arriba, cap referència no es presenta com a acreditable", async () => {
    respond({ [`${BASE}/knowledge/snapshot`]: new Error("503") });
    render(<ExperiencePage />);
    await screen.findByText("Xarxa FTTH de l'Escala");
    await flush();
    const accredited = screen.getByRole("heading", { name: "Referències acreditables" }).closest("section")!;
    expect(accredited.textContent).toContain("No s'ha pogut comprovar quines referències tenen evidència");
    expect(accredited.textContent).not.toContain("Xarxa FTTH de l'Escala");
    const declared = screen.getByRole("heading", { name: "Referències declarades sense evidència" }).closest("section")!;
    expect(declared.textContent).toContain("Xarxa FTTH de l'Escala");
    expect(declared.textContent).toContain("Centraleta de Palamós");
  });

  it("sense cap projecte: un sol buit amb la sortida real", async () => {
    respond({ [`${BASE}/projects`]: [] });
    render(<ExperiencePage />);
    expect(await screen.findByRole("heading", { name: "Cap projecte registrat" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Registra el primer projecte" })).toBeTruthy();
  });
});
