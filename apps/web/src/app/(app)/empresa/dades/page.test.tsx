import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const patch = vi.fn();
const refresh = vi.fn(async () => undefined);
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { get: (...args: unknown[]) => get(...args), patch: (...args: unknown[]) => patch(...args) },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", refresh }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import { ApiError } from "@/lib/api";

import CompanyDataPage from "./page";

const company = {
  id: "c1",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-09-13T10:00:00Z",
  created_by: null,
  updated_by: null,
  legal_name: "Empresa Demo Telecom SL",
  trade_name: null,
  tax_id: "",
  legal_form: "SL",
  incorporation_date: "2010-05-01",
  employee_count: 12,
  email: "info@demo.example",
  phone: null,
  website: null,
  address: null,
  postal_code: null,
  city: "Sabadell",
  province: "Barcelona",
  autonomous_region: "Catalunya",
  country: "ES",
  description: "Telecomunicacions",
};

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Dades generals a /empresa/dades", () => {
  beforeEach(() => {
    get.mockReset();
    patch.mockReset();
    refresh.mockClear();
    get.mockResolvedValue(company);
  });

  it("desa igual que abans: PATCH /companies/{id} amb cleanPayload i employee_count numèric", async () => {
    patch.mockImplementation(async (_path: string, payload: Record<string, unknown>) => ({ ...company, ...payload }));
    render(<CompanyDataPage />);
    const tradeName = (await screen.findByLabelText(/Nom comercial/)) as HTMLInputElement;
    fireEvent.change(tradeName, { target: { value: "Demo Telecom" } });
    fireEvent.click(screen.getByRole("button", { name: "Desa els canvis" }));
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    const [path, payload] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1");
    expect(payload.trade_name).toBe("Demo Telecom");
    expect(payload.employee_count).toBe(12);
    expect(payload.tax_id).toBeNull();
    expect(payload.legal_name).toBe("Empresa Demo Telecom SL");
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Canvis desats")).toBeTruthy();
  });

  it("un 403 no amaga «Desa»: el botó queda visible i desactivat amb una nota de permís", async () => {
    patch.mockRejectedValue(new ApiError(403, "permission_denied", "No teniu permís per fer aquesta acció."));
    render(<CompanyDataPage />);
    await screen.findByLabelText(/Nom comercial/);
    fireEvent.click(screen.getByRole("button", { name: "Desa els canvis" }));
    await flush();
    const save = screen.getByRole("button", { name: "Desa els canvis" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(/Només .* pot desar les dades generals\./)).toBeTruthy();
    expect(screen.queryByText("permission_denied")).toBeNull();
  });

  it("els errors de camp continuen sortint al camp i no desactiven el botó", async () => {
    patch.mockRejectedValue(
      new ApiError(422, "validation_error", "Revisa els camps.", [{ field: "legal_name", message: "És obligatori", type: "value_error" }]),
    );
    render(<CompanyDataPage />);
    await screen.findByLabelText(/Nom comercial/);
    fireEvent.click(screen.getByRole("button", { name: "Desa els canvis" }));
    await flush();
    expect(screen.getByText("És obligatori")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Desa els canvis" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
