import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { workspaceFixture } from "@/test/workspace-fixture";
import type { Issue } from "@/lib/issues";

/**
 * Dubtes i discrepàncies.
 *
 * El que aquests tests protegeixen són quatre frases:
 *
 *     cap dubte neix sol
 *     bloquejar la revisió final  ≠  obrir un dubte
 *     descartar  ≠  resoldre
 *     resoldre  ≠  decidir el que el dubte preguntava
 */

/* ----- Mocks --------------------------------------------------------------- */

let role = "bid_manager";
let payload: Issue[] = [];
let failWith: string | null = null;

const post = vi.fn();
const patch = vi.fn();
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
        if (path.endsWith("/issues")) return payload;
        if (path.endsWith("/documents")) return [];
        throw new Error(`GET inesperat: ${path}`);
      },
      post: (...args: unknown[]) => post(...args),
      patch: (...args: unknown[]) => patch(...args),
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

import { IssuesDrawer } from "./IssuesDrawer";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    id: "i1",
    workspace_id: "w1",
    issue_type: "internal_question",
    status: "open",
    title: "Quantes extensions hi ha",
    description: null,
    requirement_id: null,
    award_criterion_id: null,
    technical_solution_id: null,
    solution_option_id: null,
    economic_offer_line_id: null,
    dimension_id: null,
    supplier_name: null,
    owner_id: null,
    due_at: null,
    resolution: null,
    resolution_source: null,
    resolved_by: null,
    resolved_at: null,
    blocks_final_review: false,
    created_by: "u1",
    updated_by: "u1",
    created_at: "2026-09-20T10:00:00Z",
    updated_at: "2026-09-20T10:00:00Z",
    source_refs: [],
    ...overrides,
  };
}

async function mount(
  context: Record<string, string> | null = null,
  gate: { readOnly?: boolean; canDecideBlock?: boolean } = {},
) {
  const view = render(
    <IssuesDrawer
      workspace={workspaceFixture({ tender_lot_id: null })}
      open
      onClose={vi.fn()}
      context={context}
      {...gate}
    />,
  );
  await flush();
  return view;
}

function click(name: RegExp | string, scope: HTMLElement = document.body) {
  return act(async () => {
    fireEvent.click(within(scope).getByRole("button", { name }));
  });
}

async function type(label: string, value: string) {
  const field = screen.getByLabelText(new RegExp(`^${label}`));
  await act(async () => {
    fireEvent.change(field, { target: { value } });
  });
}

beforeEach(() => {
  role = "bid_manager";
  payload = [];
  failWith = null;
  post.mockReset();
  post.mockResolvedValue({});
  patch.mockReset();
  patch.mockResolvedValue({});
  del.mockReset();
  del.mockResolvedValue({});
  get.mockReset();
});

/* ----- Estat buit, càrrega i errors ---------------------------------------- */

it("sense cap dubte explica per a què serveix", async () => {
  await mount();

  expect(screen.getByText("Dubtes i discrepàncies")).toBeTruthy();
  expect(screen.getByText(/Cap dubte obert/)).toBeTruthy();
});

it("si la lectura falla ho diu i deixa tornar-ho a provar", async () => {
  failWith = "503 Service Unavailable";
  await mount();

  expect(screen.getByText(/No s'han pogut llegir els dubtes/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Torna-ho a provar" })).toBeTruthy();
});

it("llegeix sempre dins de l'empresa del context", async () => {
  await mount();

  expect(get).toHaveBeenCalledWith("/companies/c1/workspaces/w1/issues");
});

it("no obre cap pestanya nova", async () => {
  payload = [issue()];
  await mount();

  expect(screen.queryAllByRole("tab")).toHaveLength(0);
});

/* ----- Obrir-ne un --------------------------------------------------------- */

it("obrir el formulari no crea res fins que algú confirma", async () => {
  await mount();
  await click("Obre dubte");

  expect(screen.getByLabelText(/^Títol/)).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});

it("crea el dubte amb el que ha escrit la persona", async () => {
  await mount();
  await click("Obre dubte");
  await type("Títol", "Quantes extensions hi ha");
  await act(async () => {
    fireEvent.change(screen.getByLabelText("Tipus"), { target: { value: "contradiction" } });
  });
  await click("Obre'l");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/issues",
    expect.objectContaining({
      issue_type: "contradiction",
      title: "Quantes extensions hi ha",
    }),
  );
  // No hi ha manera d'obrir-lo ja tancat ni ja bloquejant.
  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect("status" in body).toBe(false);
  expect("blocks_final_review" in body).toBe(false);
});

it("sense títol no deixa continuar", async () => {
  await mount();
  await click("Obre dubte");

  expect(screen.getByRole("button", { name: "Obre'l" }).hasAttribute("disabled")).toBe(true);
});

it("obert des d'una entitat, el context ja ve preseleccionat", async () => {
  await mount({ dimension_id: "d1" });
  await click("Obre dubte");
  await type("Títol", "Quantes extensions");
  await click("Obre'l");

  const body = post.mock.calls[0][1] as Record<string, unknown>;
  expect(body.dimension_id).toBe("d1");
});

it("ofereix els cinc tipus i cap més", async () => {
  await mount();
  await click("Obre dubte");

  const select = screen.getByLabelText("Tipus") as HTMLSelectElement;
  const values = Array.from(select.options).map((option) => option.value);
  expect(values).toEqual([
    "internal_question",
    "authority_question",
    "supplier_question",
    "contradiction",
    "missing_information",
  ]);
});

/* ----- Estats -------------------------------------------------------------- */

it("els quatre estats es llegeixen diferent", async () => {
  payload = [
    issue({ id: "i1", title: "A", status: "open" }),
    issue({ id: "i2", title: "B", status: "waiting" }),
    issue({ id: "i3", title: "C", status: "resolved", resolution: "Fet" }),
    issue({ id: "i4", title: "D", status: "dismissed" }),
  ];
  await mount();
  await click("Mostra també els tancats");

  for (const label of ["Obert", "En espera", "Resolt", "Descartat"]) {
    expect(screen.getByText(label)).toBeTruthy();
  }
});

it("per defecte només ensenya els oberts", async () => {
  payload = [
    issue({ id: "i1", title: "Encara obert" }),
    issue({ id: "i2", title: "Ja resolt", status: "resolved", resolution: "Fet" }),
  ];
  await mount();

  expect(screen.getByText("Encara obert")).toBeTruthy();
  expect(screen.queryByText("Ja resolt")).toBeNull();

  await click("Mostra també els tancats");
  expect(screen.getByText("Ja resolt")).toBeTruthy();
});

/* ----- Resoldre i descartar no són el mateix -------------------------------- */

it("resoldre no deixa continuar sense escriure la resposta", async () => {
  payload = [issue()];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Resol");

  const confirm = screen.getByRole("button", { name: "Desa la resolució" });
  expect(confirm.hasAttribute("disabled")).toBe(true);

  await type("Com s'ha resolt", "Utilitzarem 181 segons l'inventari complet");
  expect(
    screen.getByRole("button", { name: "Desa la resolució" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("resoldre envia la resposta", async () => {
  payload = [issue()];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Resol");
  await type("Com s'ha resolt", "Utilitzarem 181 segons l'inventari complet");
  await click("Desa la resolució");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/issues/i1/resolve",
    expect.objectContaining({ resolution: "Utilitzarem 181 segons l'inventari complet" }),
  );
});

it("descartar no demana cap resolució", async () => {
  payload = [issue()];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Descarta");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/issues/i1/dismiss",
    {},
  );
});

it("un dubte resolt ensenya la resposta i ja no ofereix tancar-lo", async () => {
  payload = [
    issue({
      status: "resolved",
      resolution: "Utilitzarem 181 segons l'inventari complet",
      resolved_at: "2026-09-22T09:00:00Z",
    }),
  ];
  await mount();
  await click("Mostra també els tancats");
  await click("Quantes extensions hi ha");

  expect(screen.getByText(/Utilitzarem 181 segons l'inventari complet/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Resol" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Descarta" })).toBeNull();
});

/* ----- Bloquejar la revisió final ------------------------------------------ */

it("un rol de decisió pot marcar i desmarcar el bloqueig", async () => {
  payload = [issue()];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Impedeix la revisió final");

  expect(post).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/issues/i1/final-review-block",
    { blocks_final_review: true },
  );
});

it("un tècnic pot obrir i resoldre però no marcar el bloqueig", async () => {
  role = "technical";
  payload = [issue()];
  await mount();

  expect(screen.getByRole("button", { name: "Obre dubte" })).toBeTruthy();
  await click("Quantes extensions hi ha");
  expect(screen.getByRole("button", { name: "Resol" })).toBeTruthy();
  // El control no hi és: no és que estigui desactivat.
  expect(screen.queryByRole("button", { name: /Impedeix la revisió/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Ja no impedeix/ })).toBeNull();
});

/* ----- En l'anàlisi, sense GO (ajust del 22-10-2026) ----------------------- */

it("sense GO, un rol de decisió obre i resol, però el bloqueig no hi és i se li diu per què", async () => {
  payload = [issue()];
  await mount(null, { canDecideBlock: false });

  expect(screen.getByRole("button", { name: "Obre dubte" })).toBeTruthy();
  await click("Quantes extensions hi ha");
  expect(screen.getByRole("button", { name: "Resol" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Descarta" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Impedeix la revisió/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Ja no impedeix/ })).toBeNull();
  expect(screen.getByText("Decidir si impedeix la revisió final s'obre amb GO.")).toBeTruthy();

  await click("Descarta");
  expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/issues/i1/dismiss", {});
});

it("sense GO, un tècnic no veu ni el control ni l'avís del bloqueig", async () => {
  role = "technical";
  payload = [issue()];
  await mount(null, { canDecideBlock: false });

  await click("Quantes extensions hi ha");
  expect(screen.getByRole("button", { name: "Resol" })).toBeTruthy();
  expect(screen.queryByText("Decidir si impedeix la revisió final s'obre amb GO.")).toBeNull();
});

it("amb un NO-GO vigent (readOnly) tot el cos és inert i no surt cap crida", async () => {
  payload = [issue()];
  await mount(null, { readOnly: true, canDecideBlock: false });

  expect(screen.getByText("Es pot consultar; s'obre a edició amb GO")).toBeTruthy();
  const create = screen.getByRole("button", { name: "Obre dubte" }) as HTMLButtonElement;
  expect(create.closest("fieldset[disabled]")).not.toBeNull();
  create.click();
  await flush();
  expect(screen.queryByText("Nou dubte")).toBeNull();
  expect(post).not.toHaveBeenCalled();
});

it("un dubte que bloqueja ho diu", async () => {
  payload = [issue({ blocks_final_review: true })];
  await mount();

  expect(screen.getByText("Impedeix la revisió final")).toBeTruthy();
});

it("un dubte resolt conserva la decisió històrica de bloqueig", async () => {
  payload = [
    issue({ status: "resolved", resolution: "Fet", blocks_final_review: true }),
  ];
  await mount();
  await click("Mostra també els tancats");

  expect(screen.getByText("Impedeix la revisió final")).toBeTruthy();
});

/* ----- Qui només mira ------------------------------------------------------ */

it("qui només mira ho veu tot però no hi pot escriure", async () => {
  role = "viewer";
  payload = [issue({ blocks_final_review: true })];
  await mount();

  expect(screen.getByText("Quantes extensions hi ha")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Obre dubte" })).toBeNull();

  await click("Quantes extensions hi ha");
  expect(screen.queryByRole("button", { name: "Resol" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Descarta" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Impedeix la revisió/ })).toBeNull();
});

/* ----- Cites --------------------------------------------------------------- */

it("ensenya les cites que sostenen el dubte", async () => {
  payload = [
    issue({
      issue_type: "contradiction",
      source_refs: [
        {
          id: "r1",
          ref_kind: "extracted_page",
          document_id: "d1",
          document_name: "PCAP.pdf",
          file_id: "f1",
          version_number: 1,
          content_sha256: "a".repeat(64),
          extraction_id: "e1",
          page_id: "p1",
          page_number: 12,
          location_label: null,
          quote: "151 extensions",
          char_start: null,
          char_end: null,
          created_at: "2026-09-20T10:00:00Z",
        },
        {
          id: "r2",
          ref_kind: "document_file",
          document_id: "d2",
          document_name: "inventari.xlsx",
          file_id: "f2",
          version_number: 1,
          content_sha256: "b".repeat(64),
          extraction_id: null,
          page_id: null,
          page_number: null,
          location_label: "Full Càlculs, fila 42",
          quote: null,
          char_start: null,
          char_end: null,
          created_at: "2026-09-20T10:00:00Z",
        },
      ],
    }),
  ];
  await mount();
  await click("Quantes extensions hi ha");

  expect(screen.getByText("2 cites")).toBeTruthy();
  expect(screen.getAllByText(/PCAP\.pdf · p\. 12/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/Full Càlculs, fila 42/).length).toBeGreaterThan(0);
});

it("treure una cita no és esborrar-la", async () => {
  payload = [
    issue({
      source_refs: [
        {
          id: "r1",
          ref_kind: "document_file",
          document_id: "d1",
          document_name: "inventari.xlsx",
          file_id: "f1",
          version_number: 1,
          content_sha256: "a".repeat(64),
          extraction_id: null,
          page_id: null,
          page_number: null,
          location_label: null,
          quote: null,
          char_start: null,
          char_end: null,
          created_at: "2026-09-20T10:00:00Z",
        },
      ],
    }),
  ];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Treu-la");

  // Desenganxa del dubte; la cita és un recurs de la licitació i no es toca.
  expect(del).toHaveBeenCalledWith(
    "/companies/c1/workspaces/w1/issues/i1/source-refs/r1",
  );
});

/* ----- Res no es decideix sol ---------------------------------------------- */

it("res del que fa el calaix decideix cap altra cosa", async () => {
  payload = [issue({ issue_type: "contradiction", dimension_id: "d1" })];
  await mount();
  await click("Quantes extensions hi ha");
  await click("Resol");
  await type("Com s'ha resolt", "Utilitzarem 181");
  await click("Desa la resolució");

  // Resoldre el dubte no toca la magnitud, ni l'opció, ni l'oferta.
  for (const [path] of post.mock.calls as [string, unknown][]) {
    expect(path).not.toContain("/dimensions/");
    expect(path).not.toContain("/solution-options/");
    expect(path).not.toContain("/economic-offer");
  }
});

it("no ensenya cap dada econòmica encara que el dubte apunti a una línia", async () => {
  payload = [issue({ economic_offer_line_id: "ln1", title: "Preu a revisar" })];
  await mount();
  await click("Preu a revisar");

  const body = document.body.textContent ?? "";
  for (const forbidden of ["Cost", "Marge", "Markup", "€"]) {
    expect(body).not.toContain(forbidden);
  }
});
