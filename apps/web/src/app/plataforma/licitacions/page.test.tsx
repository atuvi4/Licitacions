import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { TenderListItem } from "@/lib/tenders";

const get = vi.fn();
const replace = vi.fn();
const push = vi.fn();
let search = "";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a) } };
});
const router = { replace, push, refresh: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/plataforma/licitacions",
  useSearchParams: () => new URLSearchParams(search),
  useParams: () => ({}),
  redirect: vi.fn(),
}));

import PlatformLayout from "../layout";
import PlatformTendersPage from "./page";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const operator = { id: "op", email: "admin@tuvi.app", full_name: "Albert", is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: true, memberships: [] };
const UUID = "3f1c2a9e-6b7d-4e8f-9a0b-1c2d3e4f5a6b";

function tender(overrides: Partial<TenderListItem>): TenderListItem {
  return {
    id: "t1",
    external_id: "ext-1",
    expediente_number: "2026/3964",
    title: "Servei de manteniment de la xarxa",
    status: "open",
    procedure_type: "open",
    contract_type: "services",
    estimated_value: null,
    base_budget: null,
    currency: "EUR",
    publication_date: "2026-10-01",
    deadline_at: "2026-11-15T12:00:00Z",
    nuts_code: null,
    place_of_execution: null,
    buyer: { id: "b1", name: "Ajuntament de l'Escala", tax_identifier: null, city: null, nuts_code: null },
    cpvs: [],
    lot_count: 0,
    source_publication_count: 1,
    source_code: "pscp",
    is_modified: false,
    document_count: 2,
    ...overrides,
  };
}

const page = {
  items: [tender({}), tender({ id: "t2", expediente_number: "2026/4001", title: "Subministrament de lluminàries", document_count: 0 })],
  total: 2,
  limit: 20,
  offset: 0,
};

function respond(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "/auth/me": operator,
    "/config": { environment: "local", max_upload_size_mb: 25, allowed_mime_types: [], expiry_warning_days: 30 },
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    if (path.startsWith("/tenders?")) return Promise.resolve(routes["/tenders?"] ?? page);
    const value = routes[path];
    if (value instanceof Error) return Promise.reject(value);
    return value === undefined ? Promise.reject(new ApiError(404, "not_found", "No trobat")) : Promise.resolve(value);
  });
}

async function show() {
  respond();
  render(
    <PlatformLayout>
      <PlatformTendersPage />
    </PlatformLayout>,
  );
  await flush();
  await flush();
}

beforeEach(() => {
  for (const mock of [get, replace, push]) mock.mockReset();
  search = "";
});

describe("Administració Tuvi · entrada a les licitacions públiques (ruling C4)", () => {
  it("la barra lateral té «Licitacions» i la pàgina llista les públiques recents amb la sortida als documents de cadascuna", async () => {
    await show();
    const nav = screen.getByRole("navigation", { name: "Administració Tuvi" });
    const link = within(nav).getByRole("link", { name: /Licitacions/ });
    expect(link.getAttribute("href")).toBe("/plataforma/licitacions");
    expect(link.getAttribute("aria-current")).toBe("page");
    // Reutilitza el catàleg públic, ordenat per les vistes més recentment.
    const tendersCall = get.mock.calls.map(([path]) => String(path)).find((path) => path.startsWith("/tenders?"))!;
    expect(tendersCall).toContain("sort=recent");
    expect(tendersCall).not.toContain("q=");
    const table = screen.getByRole("table", { name: "Licitacions públiques" });
    const row = within(table).getByText("Servei de manteniment de la xarxa").closest("tr")!;
    expect(row.textContent).toContain("2026/3964");
    expect(row.textContent).toContain("Ajuntament de l'Escala");
    expect(within(row).getByRole("link", { name: /Documents/ }).getAttribute("href")).toBe("/plataforma/licitacions/t1/documents");
    expect(within(within(table).getByText("Subministrament de lluminàries").closest("tr")!).getByRole("link", { name: /Documents/ }).getAttribute("href")).toBe(
      "/plataforma/licitacions/t2/documents",
    );
  });

  it("la cerca per expedient o títol passa pel catàleg (`q`) i queda a la URL", async () => {
    await show();
    fireEvent.change(screen.getByRole("searchbox", { name: "Cerca per expedient, títol o identificador" }), { target: { value: "2026/3964" } });
    fireEvent.submit(screen.getByRole("search", { name: "Cerca de licitacions" }));
    expect(replace).toHaveBeenCalledWith("/plataforma/licitacions?q=2026%2F3964", { scroll: false });
  });

  it("amb `q` a la URL, la llista és la de la cerca", async () => {
    search = "q=3964";
    await show();
    const tendersCall = get.mock.calls.map(([path]) => String(path)).find((path) => path.startsWith("/tenders?"))!;
    expect(tendersCall).toContain("q=3964");
  });

  it("un identificador enganxat porta directament als documents d'aquella licitació", async () => {
    await show();
    fireEvent.change(screen.getByRole("searchbox", { name: "Cerca per expedient, títol o identificador" }), { target: { value: ` ${UUID} ` } });
    fireEvent.submit(screen.getByRole("search", { name: "Cerca de licitacions" }));
    expect(push).toHaveBeenCalledWith(`/plataforma/licitacions/${UUID}/documents`);
    expect(replace).not.toHaveBeenCalled();
  });

  it("sense resultats ho diu i no inventa cap fila", async () => {
    search = "q=inexistent";
    respond({ "/tenders?": { items: [], total: 0, limit: 20, offset: 0 } });
    render(
      <PlatformLayout>
        <PlatformTendersPage />
      </PlatformLayout>,
    );
    await flush();
    await flush();
    expect(screen.getByText(/Cap licitació pública coincideix/)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
