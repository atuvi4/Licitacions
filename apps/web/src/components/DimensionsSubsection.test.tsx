import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { workspaceFixture } from "@/test/workspace-fixture";
import type { Dimension, DimensionObservation } from "@/lib/dimensions";

/**
 * Dimensionament: el plec diu 151, l'inventari diu 181.
 *
 * El que aquests tests protegeixen no és el disseny de la pantalla sinó una
 * frase: **el sistema no tria.** Ensenya les dues xifres, diu d'on surt
 * cadascuna, i espera que una persona en fixi una explicant per què.
 */

/* ----- Mocks --------------------------------------------------------------- */

let role = "technical";
let dimensions: Dimension[] = [];

const post = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const get = async (path: string) => {
    if (path.endsWith("/dimensions")) return dimensions;
    if (path.endsWith("/documents")) return [];
    throw new Error(`GET inesperat: ${path}`);
  };
  return {
    ...actual,
    api: { ...actual.api, get, post: (...args: unknown[]) => post(...args) },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { id: "m1", company_id: "c1", role, is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
vi.mock("@/components/ReadinessProvider", () => ({
  useInvalidateReadiness: () => vi.fn(),
}));

import { DimensionsSubsection } from "./DimensionsSubsection";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

function observation(overrides: Partial<DimensionObservation> = {}): DimensionObservation {
  return {
    id: `o${Math.random()}`,
    source_kind: "internal",
    value_text: "151",
    value_num: "151.0000",
    note: null,
    created_at: "2026-09-20T10:00:00Z",
    created_by: "u1",
    source_ref: null,
    ...overrides,
  };
}

function dimension(overrides: Partial<Dimension> = {}): Dimension {
  return {
    id: "d1",
    key: "extensions",
    label: "Extensions telefòniques",
    unit: "unitats",
    scope_label: null,
    working_value_text: null,
    working_value_num: null,
    status: "undecided",
    decision_reason: null,
    decided_by: null,
    decided_at: null,
    archived_at: null,
    revision: 0,
    observations: [],
    ...overrides,
  };
}

async function mount() {
  const view = render(<DimensionsSubsection workspace={workspaceFixture({ tender_lot_id: null })} />);
  await flush();
  return view;
}

function click(name: RegExp | string, scope: HTMLElement = document.body) {
  const button = within(scope).getByRole("button", { name });
  return act(async () => {
    fireEvent.click(button);
  });
}

/**
 * `Field` posa l'asterisc de «obligatori» i el text d'ajuda dins de l'etiqueta,
 * o sigui que el nom accessible no es nomes el titol: es busca pel principi.
 */
async function type(label: string, value: string, scope: HTMLElement = document.body) {
  const field = within(scope).getByLabelText(new RegExp(`^${label}`));
  await act(async () => {
    fireEvent.change(field, { target: { value } });
  });
}

beforeEach(() => {
  role = "technical";
  dimensions = [];
  post.mockReset();
  post.mockResolvedValue({});
});

/* ----- Dues fonts que no diuen el mateix ---------------------------------- */

it("ensenya les dues xifres i no en tria cap", async () => {
  dimensions = [
    dimension({
      status: "disputed",
      observations: [
        observation({ value_text: "151", value_num: "151.0000", source_kind: "tender_document" }),
        observation({ value_text: "181", value_num: "181.0000", source_kind: "supplier" }),
      ],
    }),
  ];
  await mount();

  expect(screen.getByText("Fonts que no coincideixen")).toBeTruthy();
  expect(screen.getByText(/Les fonts diuen 151 i 181/)).toBeTruthy();
  // I cap de les dues no apareix com si fos el valor de treball.
  expect(screen.getByText("Sense valor de treball")).toBeTruthy();
});

it("no ensenya cap valor de treball mentre ningú no n'hagi triat un", async () => {
  dimensions = [dimension({ observations: [observation({ value_text: "151" })] })];
  await mount();

  expect(screen.getByText("Sense valor de treball")).toBeTruthy();
  expect(screen.getByText("Sense decidir")).toBeTruthy();
});

it("una discrepància avisa però no bloqueja", async () => {
  dimensions = [
    dimension({
      status: "disputed",
      observations: [
        observation({ value_text: "151", value_num: "151.0000" }),
        observation({ value_text: "181", value_num: "181.0000" }),
      ],
    }),
  ];
  await mount();

  const pill = screen.getByText("Fonts que no coincideixen").closest("[data-level]");
  expect(pill?.getAttribute("data-level")).toBe("attention");
  expect(document.querySelector("[data-level='blocking']")).toBeNull();
});

/* ----- Decidir ------------------------------------------------------------- */

it("no deixa decidir sense dir per què", async () => {
  dimensions = [
    dimension({
      status: "disputed",
      observations: [
        observation({ value_text: "151", value_num: "151.0000" }),
        observation({ value_text: "181", value_num: "181.0000" }),
      ],
    }),
  ];
  await mount();
  await click("Extensions telefòniques");
  await click("Fixa el valor de treball");

  await type("Valor de treball", "181");
  const confirm = screen.getByRole("button", { name: "Fixa'l" });
  expect(confirm.hasAttribute("disabled")).toBe(true);

  await type("Per què aquest", "Valor de treball segons inventari complet");
  expect(screen.getByRole("button", { name: "Fixa'l" }).hasAttribute("disabled")).toBe(false);
});

it("envia el valor, el motiu i el testimoni de la versió que s'ha llegit", async () => {
  dimensions = [
    dimension({
      revision: 7,
      status: "disputed",
      observations: [
        observation({ value_text: "151", value_num: "151.0000" }),
        observation({ value_text: "181", value_num: "181.0000" }),
      ],
    }),
  ];
  await mount();
  await click("Extensions telefòniques");
  await click("Fixa el valor de treball");
  await type("Valor de treball", "181");
  await type("Xifra", "181");
  await type("Per què aquest", "Valor de treball segons inventari complet");
  await click("Fixa'l");

  expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dimensions/d1/decide", {
    expected_revision: 7,
    working_value_text: "181",
    working_value_num: "181",
    reason: "Valor de treball segons inventari complet",
  });
});

it("conserva la font descartada després de decidir", async () => {
  dimensions = [
    dimension({
      status: "decided",
      working_value_text: "181",
      working_value_num: "181.0000",
      decision_reason: "Valor de treball segons inventari complet",
      decided_at: "2026-09-22T09:00:00Z",
      observations: [
        observation({ value_text: "151", value_num: "151.0000", source_kind: "tender_document" }),
        observation({ value_text: "181", value_num: "181.0000", source_kind: "supplier" }),
      ],
    }),
  ];
  await mount();
  await click("Extensions telefòniques");

  // Totes dues hi son, i la que no es va triar es diu, no s'amaga.
  expect(screen.getByText("151")).toBeTruthy();
  expect(screen.getAllByText("181").length).toBeGreaterThan(0);
  expect(screen.getByText("No és el valor de treball")).toBeTruthy();
  // El motiu es llegeix un cop, no dos: es informacio, no una alarma.
  expect(screen.getAllByText(/Valor de treball segons inventari complet/)).toHaveLength(1);
});

/* ----- Append-only --------------------------------------------------------- */

it("no ofereix enlloc esborrar ni editar una font", async () => {
  dimensions = [
    dimension({
      observations: [observation({ value_text: "151", note: "Plec tècnic, apartat 2.1" })],
    }),
  ];
  await mount();
  await click("Extensions telefòniques");

  const buttons = screen.getAllByRole("button").map((button) => button.textContent ?? "");
  expect(buttons.some((label) => /esborra|elimina|edita/i.test(label))).toBe(false);
});

/* ----- Registrar una font -------------------------------------------------- */

it("registra què diu una font sense dir que sigui el valor", async () => {
  dimensions = [dimension({ revision: 3 })];
  await mount();
  await click("Extensions telefòniques");
  await click("Afegeix una font");

  await type("Què hi diu", "181 extensions");
  await type("Xifra", "181");
  await click("Afegeix la font");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/dimensions/d1/observations",
    expect.objectContaining({
      expected_revision: 3,
      value_text: "181 extensions",
      value_num: "181",
      source_kind: "tender_document",
    }),
  );
  // I el que s'ha enviat no fixa cap valor de treball ni cap estat.
  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect("status" in body).toBe(false);
  expect("working_value_text" in body).toBe(false);
});

it("deixa registrar una font sense xifra", async () => {
  dimensions = [dimension()];
  await mount();
  await click("Extensions telefòniques");
  await click("Afegeix una font");
  await type("Què hi diu", "cent vuitanta-una");
  await click("Afegeix la font");

  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect(body.value_num).toBeNull();
});

/* ----- Crear ---------------------------------------------------------------*/

it("crear una magnitud no demana cap valor", async () => {
  await mount();
  await click("Afegeix una magnitud");

  expect(screen.getByLabelText(/^Nom/)).toBeTruthy();
  expect(screen.queryByLabelText(/^Valor de treball/)).toBeNull();
  expect(screen.queryByLabelText(/^Per què aquest/)).toBeNull();
});

it("dedueix la clau del nom quan no se'n dona cap", async () => {
  await mount();
  await click("Afegeix una magnitud");
  await type("Nom", "Extensions telefòniques");
  await click("Crea-la");

  expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dimensions", {
    key: "extensions_telefòniques",
    label: "Extensions telefòniques",
    unit: null,
    scope_label: null,
  });
});

/* ----- Rols ---------------------------------------------------------------- */

it("qui només mira ho veu tot però no hi pot escriure", async () => {
  role = "viewer";
  dimensions = [
    dimension({
      status: "decided",
      working_value_text: "181",
      decision_reason: "Inventari complet",
      decided_at: "2026-09-22T09:00:00Z",
      observations: [observation({ value_text: "151" }), observation({ value_text: "181" })],
    }),
  ];
  await mount();

  expect(screen.getByText("Decidida")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Afegeix una magnitud" })).toBeNull();

  await click("Extensions telefòniques");
  expect(screen.queryByRole("button", { name: /Fixa el valor de treball/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Afegeix una font" })).toBeNull();
});

/* ----- Sense res ----------------------------------------------------------- */

it("quan no hi ha cap magnitud explica per a què serveix", async () => {
  await mount();

  expect(screen.getByText(/Cap magnitud encara/)).toBeTruthy();
});
