import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { workspaceFixture } from "@/test/workspace-fixture";
import type { SolutionOption, SolutionOptionList } from "@/lib/solutionOptions";

/**
 * Opcions i cotitzacions: Proveïdor A o Proveïdor B.
 *
 * El que aquests tests protegeixen no és el disseny sinó tres frases:
 *
 *     una opció  ≠  una línia d'oferta
 *     «sense decidir»  ≠  «rebutjada»
 *     triar-ne una  ≠  descartar les altres
 *
 * I una porta de seguretat: **el cost no apareix per a qui no el pot veure**,
 * ni com a «0 €» ni com a «ocult».
 */

/* ----- Mocks --------------------------------------------------------------- */

let role = "bid_manager";
let payload: SolutionOptionList = { can_view_internal_economics: true, items: [] };
let failWith: string | null = null;

const post = vi.fn();
const get = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: async (path: string) => {
        get(path);
        if (failWith) throw new Error(failWith);
        if (path.endsWith("/solution-options")) return payload;
        throw new Error(`GET inesperat: ${path}`);
      },
      post: (...args: unknown[]) => post(...args),
    },
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

import { SolutionOptionsSubsection } from "./SolutionOptionsSubsection";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

function option(overrides: Partial<SolutionOption> = {}): SolutionOption {
  return {
    id: "o1",
    workspace_id: "w1",
    technical_solution_id: null,
    requirement_id: null,
    requirement_is_effective: null,
    supplier_name: "Proveïdor A",
    product_name: "Terminal X",
    description: null,
    quantity: "151.0000",
    unit: "unitats",
    pricing_basis: null,
    duration_months_override: null,
    quote_status: "draft",
    quote_date: null,
    quote_reference: null,
    quote_source: null,
    selection: "undecided",
    selection_reason: null,
    selected_by: null,
    selected_at: null,
    archived_at: null,
    revision: 0,
    internal_economics: null,
    ...overrides,
  };
}

function list(items: SolutionOption[], canSeeCost = true): SolutionOptionList {
  return { can_view_internal_economics: canSeeCost, items };
}

async function mount() {
  const view = render(
    <SolutionOptionsSubsection workspace={workspaceFixture({ tender_lot_id: null })} />,
  );
  await flush();
  return view;
}

function click(name: RegExp | string, scope: HTMLElement = document.body) {
  const button = within(scope).getByRole("button", { name });
  return act(async () => {
    fireEvent.click(button);
  });
}

/** `Field` posa l'asterisc i el text d'ajuda dins de l'etiqueta: es cerca pel principi. */
async function type(label: string, value: string) {
  const field = screen.getByLabelText(new RegExp(`^${label}`));
  await act(async () => {
    fireEvent.change(field, { target: { value } });
  });
}

beforeEach(() => {
  role = "bid_manager";
  payload = list([]);
  failWith = null;
  post.mockReset();
  post.mockResolvedValue({});
  get.mockReset();
});

/* ----- Estat buit i errors ------------------------------------------------- */

it("sense cap opció ho diu i prou", async () => {
  await mount();

  expect(screen.getByText("Cap opció encara.")).toBeTruthy();
});

it("si la lectura falla ho diu i deixa tornar-ho a provar", async () => {
  failWith = "503 Service Unavailable";
  await mount();

  expect(screen.getByText(/No s'han pogut llegir les opcions/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Torna-ho a provar" })).toBeTruthy();
});

it("viu dins de Solució tècnica i no obre cap pestanya nova", async () => {
  payload = list([option()]);
  await mount();

  expect(screen.getByText("Opcions i cotitzacions")).toBeTruthy();
  expect(screen.queryAllByRole("tab")).toHaveLength(0);
});

/* ----- El cost no surt per a qui no el pot veure ---------------------------- */

it("amb rol econòmic ensenya el cost", async () => {
  payload = list([
    option({ internal_economics: { unit_cost_ex_vat: "12.345678", internal_note: null } }),
  ]);
  await mount();

  expect(screen.getByText("Cost unitari")).toBeTruthy();
  expect(screen.getByText("12.345678")).toBeTruthy();
});

it("sense permís la columna del cost no existeix", async () => {
  role = "technical";
  payload = list([option({ internal_economics: null })], false);
  await mount();

  expect(screen.queryByText("Cost unitari")).toBeNull();
  // Ni un zero, ni un «ocult»: un zero seria una xifra falsa i «ocult» diria
  // que n'hi ha una.
  expect(screen.queryByText("0")).toBeNull();
  expect(screen.queryByText(/ocult/i)).toBeNull();
  expect(screen.queryByText(/0 €/)).toBeNull();
});

it("el formulari de creació no ofereix el cost a qui no el pot escriure", async () => {
  role = "technical";
  payload = list([], false);
  await mount();
  await click("Afegeix opció");

  expect(screen.getByLabelText(/^Proveïdor/)).toBeTruthy();
  expect(screen.queryByLabelText(/^Cost unitari/)).toBeNull();
  expect(screen.queryByText("Economia interna")).toBeNull();
});

it("un tècnic no envia mai el cost en crear", async () => {
  role = "technical";
  payload = list([], false);
  await mount();
  await click("Afegeix opció");
  await type("Proveïdor", "Proveïdor A");
  await click("Crea-la");

  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect("internal_economics" in body).toBe(false);
  expect("unit_cost_ex_vat" in body).toBe(false);
});

it("un rol econòmic pot crear amb cost", async () => {
  await mount();
  await click("Afegeix opció");
  await type("Proveïdor", "Proveïdor A");
  await type("Cost unitari sense IVA", "12.345678");
  await click("Crea-la");

  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect(body.internal_economics).toEqual({ unit_cost_ex_vat: "12.345678" });
});

/* ----- Triar i rebutjar ---------------------------------------------------- */

it("seleccionar i rebutjar són dues accions separades i explícites", async () => {
  payload = list([option()]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByRole("button", { name: "Selecciona" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rebutja" })).toBeTruthy();
  // Cap casella excloent que faci semblar que triar-ne una descarta les altres.
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
});

it("seleccionar envia el testimoni de la versió llegida", async () => {
  payload = list([option({ revision: 7 })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Selecciona");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/solution-options/o1/selection",
    { expected_revision: 7, selection: "selected" },
  );
});

it("seleccionar una opció no toca cap germana", async () => {
  payload = list([
    option({ id: "o1", supplier_name: "Sewan" }),
    option({ id: "o2", supplier_name: "Cable" }),
    option({ id: "o3", supplier_name: "Orange" }),
  ]);
  await mount();
  await click("Sewan · Terminal X");
  await click("Selecciona");

  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][0]).toContain("/o1/selection");
});

it("rebutjar no deixa continuar sense motiu", async () => {
  payload = list([option()]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Rebutja");

  const confirm = screen.getByRole("button", { name: "Rebutja-la" });
  expect(confirm.hasAttribute("disabled")).toBe(true);

  await type("Per què es descarta", "Prestacions inferiors a la configuració escollida.");
  expect(screen.getByRole("button", { name: "Rebutja-la" }).hasAttribute("disabled")).toBe(false);
});

it("rebutjar envia el motiu", async () => {
  payload = list([option({ revision: 3 })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Rebutja");
  await type("Per què es descarta", "Prestacions inferiors a la configuració escollida.");
  await click("Rebutja-la");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/solution-options/o1/selection",
    {
      expected_revision: 3,
      selection: "rejected",
      reason: "Prestacions inferiors a la configuració escollida.",
    },
  );
});

it("una opció rebutjada conserva i ensenya el motiu", async () => {
  payload = list([
    option({
      selection: "rejected",
      selection_reason: "Prestacions inferiors a la configuració escollida.",
    }),
  ]);
  await mount();

  expect(screen.getByText("Rebutjada")).toBeTruthy();
  await click("Proveïdor A · Terminal X");
  expect(
    screen.getByText(/Prestacions inferiors a la configuració escollida\./),
  ).toBeTruthy();
});

it("«sense decidir» i «rebutjada» es llegeixen diferent", async () => {
  payload = list([
    option({ id: "o1", supplier_name: "Sense mirar" }),
    option({ id: "o2", supplier_name: "Descartada", selection: "rejected", selection_reason: "x" }),
  ]);
  await mount();

  expect(screen.getByText("Sense decidir")).toBeTruthy();
  expect(screen.getByText("Rebutjada")).toBeTruthy();
});

/* ----- Cotitzacions -------------------------------------------------------- */

it("cada estat de cotització té el seu nom", async () => {
  payload = list([
    option({ id: "o1", supplier_name: "A", quote_status: "draft" }),
    option({ id: "o2", supplier_name: "B", quote_status: "requested" }),
    option({ id: "o3", supplier_name: "C", quote_status: "pending" }),
    option({ id: "o4", supplier_name: "D", quote_status: "received" }),
    option({ id: "o5", supplier_name: "E", quote_status: "expired" }),
    option({ id: "o6", supplier_name: "F", quote_status: "declined" }),
  ]);
  await mount();

  for (const label of [
    "Esborrany",
    "Demanada",
    "Pendent",
    "Rebuda",
    "Caducada",
    "Declinada",
  ]) {
    expect(screen.getByText(label)).toBeTruthy();
  }
});

it("«rebuda» no es presenta com a aprovada", async () => {
  payload = list([option({ quote_status: "received" })]);
  await mount();

  expect(screen.getByText("Rebuda")).toBeTruthy();
  expect(screen.queryByText(/aprovada|validada|OK/i)).toBeNull();
});

/* ----- Arxivar ------------------------------------------------------------- */

it("arxivar és una acció explícita i no hi ha cap esborrat", async () => {
  payload = list([option({ revision: 2 })]);
  await mount();
  await click("Proveïdor A · Terminal X");

  const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
  expect(labels.some((l) => /esborra|elimina/i.test(l))).toBe(false);

  await click("Arxiva");
  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/solution-options/o1/archive",
    { expected_revision: 2 },
  );
});

/* ----- Rols ---------------------------------------------------------------- */

it("qui només mira ho veu tot però no hi pot escriure", async () => {
  role = "viewer";
  payload = list([option({ selection: "selected" })], false);
  await mount();

  expect(screen.getByText("Seleccionada")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Afegeix opció" })).toBeNull();

  await click("Proveïdor A · Terminal X");
  expect(screen.queryByRole("button", { name: "Selecciona" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Rebutja" })).toBeNull();
});

it("un tècnic pot configurar tota la part no sensible", async () => {
  role = "technical";
  payload = list([option()], false);
  await mount();

  expect(screen.getByRole("button", { name: "Afegeix opció" })).toBeTruthy();
  await click("Proveïdor A · Terminal X");
  expect(screen.getByRole("button", { name: "Selecciona" })).toBeTruthy();
});

/* ----- Context d'empresa --------------------------------------------------- */

it("llegeix sempre dins de l'empresa del context", async () => {
  payload = list([option()]);
  await mount();

  expect(get).toHaveBeenCalledWith("/companies/c1/workspaces/w1/solution-options");
});

/* ----- El que no ha de passar ---------------------------------------------- */

it("no crea cap línia econòmica ni toca cap dimensió", async () => {
  payload = list([option({ quantity: "181.0000" })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Selecciona");

  for (const [path] of post.mock.calls as [string, unknown][]) {
    expect(path).not.toContain("economic-offer");
    expect(path).not.toContain("dimensions");
  }
});

it("diu quan el requisit enllaçat ha deixat de ser vigent, sense amagar l'opció", async () => {
  payload = list([option({ requirement_id: "r1", requirement_is_effective: false })]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText(/ja no és del conjunt vigent/)).toBeTruthy();
});
