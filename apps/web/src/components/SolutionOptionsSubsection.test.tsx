import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { workspaceFixture } from "@/test/workspace-fixture";
import type {
  OptionDimension,
  SolutionOption,
  SolutionOptionList,
} from "@/lib/solutionOptions";

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
let dimensionsPayload: OptionDimension[] = [];

const post = vi.fn();
const put = vi.fn();
const del = vi.fn();
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
        if (path.endsWith("/dimensions")) return dimensionsPayload;
        throw new Error(`GET inesperat: ${path}`);
      },
      post: (...args: unknown[]) => post(...args),
      put: (...args: unknown[]) => put(...args),
      delete: (...args: unknown[]) => del(...args),
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
    dimensions: [],
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
  dimensionsPayload = [];
  post.mockReset();
  post.mockResolvedValue({});
  put.mockReset();
  put.mockResolvedValue({});
  del.mockReset();
  del.mockResolvedValue({});
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


/* ----- De què depèn una opció (S3-S7b) ------------------------------------- */

function dimension(overrides: Partial<OptionDimension> = {}): OptionDimension {
  return {
    id: "d1",
    key: "extensions",
    label: "Extensions",
    unit: "unitats",
    scope_label: null,
    status: "decided",
    working_value_text: "181",
    working_value_num: "181.0000",
    archived_at: null,
    revision: 1,
    ...overrides,
  };
}

it("ensenya de quines magnituds depèn una opció", async () => {
  payload = list([
    option({
      dimensions: [
        dimension({ id: "d1", label: "Extensions", working_value_text: "181" }),
        dimension({ id: "d2", key: "seus", label: "Seus", working_value_text: "26", unit: null }),
      ],
    }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Depèn de")).toBeTruthy();
  expect(screen.getByText("Extensions")).toBeTruthy();
  expect(screen.getByText("Seus")).toBeTruthy();
  expect(screen.getByText("181 unitats")).toBeTruthy();
  expect(screen.getByText("26")).toBeTruthy();
});

it("sense cap dependència ho diu", async () => {
  payload = list([option()]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText(/Cap magnitud encara/)).toBeTruthy();
});

it("la magnitud i la quantitat de l'opció són dos camps independents", async () => {
  /**
   * La magnitud diu 181 i l'opció en diu 40. Es veuen totes dues i no hi ha cap
   * botó que digui «fes servir 181»: copiar-la faria que una oferta canviés de
   * números sense que ningú no ho hagués decidit.
   */
  payload = list([
    option({ quantity: "40.0000", dimensions: [dimension({ working_value_text: "181" })] }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("40.0000")).toBeTruthy();
  expect(screen.getByText("181 unitats")).toBeTruthy();
  const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
  expect(labels.some((l) => /utilitza|fes servir|aplica|copia|sincronitza/i.test(l))).toBe(false);
});

it("després que la magnitud canviï, la quantitat de l'opció continua sent l'antiga", async () => {
  /**
   * L'estat que arriba del servidor quan algú ha decidit 209 però ningú no ha
   * tocat l'opció. La pantalla ha d'ensenyar les dues xifres tal com són: la
   * quantitat surt de `option.quantity` i el valor surt de la magnitud, i cap
   * de les dues no es deriva de l'altra.
   */
  payload = list([
    option({
      quantity: "181.0000",
      dimensions: [dimension({ working_value_text: "209", working_value_num: "209.0000" })],
    }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("209 unitats")).toBeTruthy();
  expect(screen.getByText("181.0000")).toBeTruthy();
});

it("el selector només ofereix magnituds actives i no demana cap UUID", async () => {
  dimensionsPayload = [
    dimension({ id: "d1", label: "Extensions" }),
    dimension({ id: "d2", key: "seus", label: "Seus", archived_at: "2026-09-01T00:00:00Z" }),
  ];
  payload = list([option()]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Relaciona una magnitud");
  await flush();

  const select = screen.getByLabelText("Magnitud") as HTMLSelectElement;
  const labels = Array.from(select.options).map((o) => o.textContent ?? "");
  expect(labels.some((l) => l.includes("Extensions"))).toBe(true);
  expect(labels.some((l) => l.includes("Seus"))).toBe(false);
  // Cap camp on escriure un identificador.
  expect(screen.queryByLabelText(/uuid|identificador/i)).toBeNull();
});

it("no torna a oferir una magnitud que ja hi és", async () => {
  dimensionsPayload = [dimension({ id: "d1", label: "Extensions" })];
  payload = list([option({ dimensions: [dimension({ id: "d1", label: "Extensions" })] })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Relaciona una magnitud");
  await flush();

  const select = screen.getByLabelText("Magnitud") as HTMLSelectElement;
  const values = Array.from(select.options).map((o) => o.value).filter(Boolean);
  expect(values).toEqual([]);
});

it("relacionar envia el testimoni de la versió llegida", async () => {
  dimensionsPayload = [dimension({ id: "d1" })];
  payload = list([option({ revision: 5 })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Relaciona una magnitud");
  await flush();

  const select = screen.getByLabelText("Magnitud");
  await act(async () => {
    fireEvent.change(select, { target: { value: "d1" } });
  });
  await click("Relaciona-la");

  expect(put).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/solution-options/o1/dimensions/d1",
    { expected_revision: 5 },
  );
});

it("treure una dependència passa pel testimoni i no esborra res més", async () => {
  payload = list([option({ revision: 4, dimensions: [dimension({ id: "d1" })] })]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Treu-la");

  expect(del).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/solution-options/o1/dimensions/d1?expected_revision=4",
  );
});

it("una magnitud arxivada que ja hi era es veu marcada", async () => {
  payload = list([
    option({ dimensions: [dimension({ archived_at: "2026-09-01T00:00:00Z" })] }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Arxivada")).toBeTruthy();
  // I es pot treure: es com se'n neteja el rastre.
  expect(screen.getByRole("button", { name: "Treu-la" })).toBeTruthy();
});

it("una magnitud en disputa es pot relacionar i es llegeix com a tal", async () => {
  payload = list([
    option({
      dimensions: [dimension({ status: "disputed", working_value_text: null })],
    }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Fonts que no coincideixen")).toBeTruthy();
  expect(screen.getByText("Sense valor de treball")).toBeTruthy();
});

it("qui només mira veu les dependències però no les pot tocar", async () => {
  role = "viewer";
  payload = list([option({ dimensions: [dimension()] })], false);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Extensions")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Relaciona una magnitud" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Treu-la" })).toBeNull();
});

it("un tècnic sí que les pot gestionar", async () => {
  role = "technical";
  payload = list([option({ dimensions: [dimension()] })], false);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByRole("button", { name: "Relaciona una magnitud" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Treu-la" })).toBeTruthy();
});

it("les dependències no obren cap porta al cost", async () => {
  role = "technical";
  payload = list([option({ dimensions: [dimension()], internal_economics: null })], false);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Extensions")).toBeTruthy();
  expect(screen.queryByText("Cost unitari")).toBeNull();
  expect(document.body.textContent).not.toMatch(/12\.345678/);
});

it("una opció arxivada conserva les dependències però no en rep de noves", async () => {
  payload = list([
    option({ archived_at: "2026-09-01T00:00:00Z", dimensions: [dimension()] }),
  ]);
  await mount();
  await click("Proveïdor A · Terminal X");

  expect(screen.getByText("Extensions")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Relaciona una magnitud" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Treu-la" })).toBeNull();
});

it("res d'aquesta fase crea cap línia econòmica", async () => {
  dimensionsPayload = [dimension({ id: "d1" })];
  payload = list([option()]);
  await mount();
  await click("Proveïdor A · Terminal X");
  await click("Relaciona una magnitud");
  await flush();

  const labels = screen.getAllByRole("button").map((b) => b.textContent ?? "");
  expect(labels.some((l) => /oferta|línia|line|preu/i.test(l))).toBe(false);
  for (const [path] of put.mock.calls as [string, unknown][]) {
    expect(path).not.toContain("economic-offer");
  }
});
