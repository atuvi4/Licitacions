import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UNACCENTED_LABELS, accentedVocabularyFixture, vocabularyFixture } from "@/test/knowledge-fixture";
import type { Service } from "@/lib/types";

/**
 * La pàgina de serveis: el formulari porta la cobertura territorial (0047),
 * que només viatja si es toca. Un servei antic no està obligat a dir-ne res.
 */

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args), delete: vi.fn() } };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", membership: { role: "owner", company: { id: "c1", legal_name: "Empresa Demo SL", trade_name: null } } }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import ServicesPage from "./page";

const BASE = "/companies/c1";
const audit = { created_at: "2026-09-29T00:00:00Z", updated_at: "2026-09-29T00:00:00Z", created_by: null, updated_by: null };

function service(overrides: Partial<Service> = {}): Service {
  return {
    id: "svc-1",
    company_id: "c1",
    name: "FTTH",
    category: null,
    description: null,
    specialization_level: null,
    active: true,
    notes: null,
    service_line_code: "access_ftth",
    delivery_mode: "own",
    ...audit,
    ...overrides,
  };
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

function respond(services: Service[]) {
  get.mockImplementation((path: string) => {
    if (path === `${BASE}/services`) return Promise.resolve(services);
    if (path === `${BASE}/knowledge/vocabulary`) return Promise.resolve(vocabularyFixture);
    return Promise.reject(new Error(`inesperat: ${path}`));
  });
}

async function renderPage() {
  const view = render(<ServicesPage />);
  await screen.findByRole("button", { name: "Nou servei" });
  await flush();
  return view;
}

describe("Serveis: cobertura territorial", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    patch.mockReset();
    post.mockResolvedValue({});
    patch.mockResolvedValue({});
  });

  it("un servei antic s'edita sense enviar cap cobertura; si es toca, s'envia sencera", async () => {
    respond([service()]);
    await renderPage();
    expect(screen.queryByText(/Cobertura/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edita" }));
    let dialog = screen.getByRole("dialog", { name: "Edita el servei" });
    expect((within(dialog).getByLabelText(/^Completesa/) as HTMLSelectElement).value).toBe("unknown");
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: "FTTH Costa Brava" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0][0]).toBe(`${BASE}/services/svc-1`);
    expect(patch.mock.calls[0][1]).not.toHaveProperty("coverage");
    expect(patch.mock.calls[0][1]).toMatchObject({ name: "FTTH Costa Brava", service_line_code: "access_ftth" });

    patch.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Edita" }));
    dialog = screen.getByRole("dialog", { name: "Edita el servei" });
    fireEvent.change(within(dialog).getByLabelText(/^Completesa/), { target: { value: "partial" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Afegeix una àrea" }));
    fireEvent.change(within(dialog).getByLabelText("Nom de l'àrea 1"), { target: { value: "Palamós" } });
    // Municipi: només el codi INE; el sistema l'hi posa el formulari.
    fireEvent.change(within(dialog).getByLabelText("Codi INE de l'àrea 1"), { target: { value: "17118" } });
    expect(within(dialog).queryByLabelText(/Sistema de codi/)).toBeNull();
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(patch.mock.calls[0][1]).toMatchObject({
      coverage: { completeness: "partial", areas: [{ kind: "municipality", label: "Palamós", code: "17118", code_system: "INE" }] },
    });
  });

  it("mostra la cobertura que ja té cada servei, i l'editor la carrega", async () => {
    respond([
      service({
        coverage: { completeness: "complete", areas: [{ kind: "municipality", label: "L'Escala", code: null, code_system: null }] },
      }),
    ]);
    await renderPage();
    expect(screen.getByText("Cobertura completa: L'Escala")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edita" }));
    const dialog = screen.getByRole("dialog", { name: "Edita el servei" });
    expect((within(dialog).getByLabelText(/^Completesa/) as HTMLSelectElement).value).toBe("complete");
    expect((within(dialog).getByLabelText("Nom de l'àrea 1") as HTMLInputElement).value).toBe("L'Escala");
    // Treure l'única àrea i deixar «completa» és una afirmació («enlloc»): s'envia tal qual.
    fireEvent.click(within(dialog).getByRole("button", { name: "Treu l'àrea 1" }));
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(patch.mock.calls[0][1]).toMatchObject({ coverage: { completeness: "complete", areas: [] } });
  });

  it("un servei nou neix sense cobertura si no se'n diu res", async () => {
    respond([]);
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Nou servei" }));
    const dialog = screen.getByRole("dialog", { name: "Nou servei" });
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: "Centraleta" } });
    // La línia es tria al combobox cercable escrivint-hi i clicant l'opció.
    fireEvent.change(within(dialog).getByRole("combobox", { name: /Línia de servei/ }), { target: { value: "centraleta" } });
    fireEvent.click(within(dialog).getByRole("option", { name: "Centraleta i veu IP" }));
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][1]).toMatchObject({ name: "Centraleta", service_line_code: "voice_pbx" });
    expect(post.mock.calls[0][1]).not.toHaveProperty("coverage");
  });
});

describe("Serveis: rètols del vocabulari amb accents (copy v3)", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("la taula i el formulari mostren els rètols accentuats, també els que només coneix l'API", async () => {
    get.mockImplementation((path: string) => {
      if (path === `${BASE}/services`) {
        return Promise.resolve([
          service({ id: "svc-trunk", name: "Troncals", service_line_code: "voice_trunk" }),
          service({ id: "svc-radio", name: "Enllaços", service_line_code: "radio_link" }),
          service({ id: "svc-sat", name: "Backhaul", service_line_code: "satellite_backhaul" }),
        ]);
      }
      if (path === `${BASE}/knowledge/vocabulary`) return Promise.resolve(accentedVocabularyFixture);
      return Promise.reject(new Error(`inesperat: ${path}`));
    });
    const { container } = await renderPage();
    for (const label of ["Troncals de veu i numeració", "Radioenllaços", "Retorn per satèl·lit"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(container.textContent).not.toMatch(UNACCENTED_LABELS);

    fireEvent.click(screen.getByRole("button", { name: "Nou servei" }));
    const dialog = screen.getByRole("dialog", { name: "Nou servei" });
    const input = within(dialog).getByRole("combobox", { name: /Línia de servei/ });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const offered = within(within(dialog).getByRole("listbox")).getAllByRole("option").map((option) => option.textContent);
    expect(offered).toEqual(accentedVocabularyFixture.service_lines.map((line) => line.label));
    expect(offered.join(" | ")).not.toMatch(UNACCENTED_LABELS);
  });
});
