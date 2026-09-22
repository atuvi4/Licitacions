import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readinessFixture } from "@/test/readiness-fixture";
import { analysisFixture, requirementFixture, solutionFixture } from "@/test/technical-fixture";
import { workspaceFixture } from "@/test/workspace-fixture";
import type { Requirement } from "@/lib/requirements";
import type { TechnicalSolution } from "@/lib/technical";

/* ----- Mocks --------------------------------------------------------------- */

let role = "technical";
let search = "pas=preparar&area=solucio";
let requirements: Requirement[] = [];
let solutions: TechnicalSolution[] = [];
let readiness = readinessFixture();
let offerLots: Array<{ tender_lot_id: string | null; archived_at: string | null }> | null = null;
let canSelectLots = false;
// La subseccio de Dimensionament viu dins d'aquesta seccio i llegeix la seva
// propia llista. Aquests tests no la proven: nomes cal que hi sigui.
const dimensions: unknown[] = [];

const post = vi.fn();
const patch = vi.fn();
const getReadiness = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const get = async (path: string) => {
    if (path.includes("/final-readiness")) {
      getReadiness(path);
      return readiness;
    }
    if (path.includes("/analysis/requirements")) return analysisFixture(requirements);
    if (path.endsWith("/technical-solutions")) return solutions;
    if (path.endsWith("/dimensions")) return dimensions;
    if (path.endsWith("/economic-offer")) {
      if (!offerLots) throw new actual.ApiError(404, "not_found", "Sense oferta");
      return { can_select_lots: canSelectLots, lots: offerLots };
    }
    throw new Error(`GET inesperat: ${path}`);
  };
  return {
    ...actual,
    api: { ...actual.api, get, post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args) },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { id: "m1", company_id: "c1", role, is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/candidatures/w1",
  useSearchParams: () => new URLSearchParams(search),
}));

import { ApiError } from "@/lib/api";

import { ReadinessProvider } from "./ReadinessProvider";
import { TechnicalSolutionSection } from "./TechnicalSolutionSection";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const members = [
  { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  { id: "u2", email: "marc@example.org", full_name: "Marc Roca" },
];

const registerLeaveGuard = vi.fn();

/** Readiness coherent amb el que la pantalla ha de pintar: si no, salta el gate d'abast. */
function readinessWith(inScope: number, technical: Partial<ReturnType<typeof readinessFixture>["technical"]> = {}) {
  const base = readinessFixture().technical;
  return readinessFixture({ technical: { ...base, requirements_in_scope: inScope, ...technical } });
}

async function mount(overrides: Partial<Parameters<typeof workspaceFixture>[0]> = {}) {
  const workspace = workspaceFixture({ members, tender_lot_id: null, ...overrides });
  const view = render(
    <ReadinessProvider companyId="c1" workspaceId="w1">
      <TechnicalSolutionSection workspace={workspace} registerLeaveGuard={registerLeaveGuard} />
    </ReadinessProvider>,
  );
  await flush();
  return view;
}

/** La fila d'un requisit. Amb l'editor obert el títol també surt al context: la cel·la és la primera. */
function rowOf(text: string): HTMLElement {
  return screen.getAllByText(text)[0].closest("tr") as HTMLElement;
}

function click(name: RegExp | string, scope: HTMLElement | Document = document.body as HTMLElement) {
  const button = within(scope as HTMLElement).getByRole("button", { name });
  return act(async () => {
    fireEvent.click(button);
  });
}

function filter(name: string) {
  return click(name, screen.getByRole("group", { name: "Filtra les files" }));
}

beforeEach(() => {
  role = "technical";
  search = "pas=preparar&area=solucio";
  requirements = [requirementFixture({ id: "r1" }), requirementFixture({ id: "r2", title: "Garantia de 36 mesos", category: "contract_condition" })];
  solutions = [solutionFixture({ id: "s1", requirement_id: "r1" })];
  readiness = readinessWith(2);
  offerLots = null;
  canSelectLots = false;
  post.mockReset();
  patch.mockReset();
  getReadiness.mockReset();
  registerLeaveGuard.mockReset();
  Element.prototype.scrollIntoView = vi.fn();
});

/* ----- Dades i render ------------------------------------------------------ */

describe("Solució tècnica · taula", () => {
  it("un requisit amb solució: títol, solució, estat i responsable", async () => {
    await mount();
    const row = rowOf("Commutadors amb doble alimentació");
    expect(row.textContent).toContain("Commutació d'accés amb redundància");
    expect(row.textContent).toContain("Aruba · CX 6300M");
    expect(row.textContent).toContain("En redacció");
    expect(row.textContent).toContain("Anna Puig");
    expect(row.textContent).toContain("Obligatori · Plec tècnic · p. 14");
  });

  it("un requisit sense solució queda neutre: cap vermell, cap avís, i una sola acció", async () => {
    await mount();
    const row = rowOf("Garantia de 36 mesos");
    expect(row.textContent).toContain("Sense solució");
    expect(row.querySelectorAll("[data-level='blocking'], [data-level='attention']")).toHaveLength(0);
    expect(within(row).getByRole("button", { name: "Defineix una solució" })).toBeTruthy();
  });

  it("un requisit sense solució no genera cap problema a la preparació final", async () => {
    await mount();
    // El servidor diu «a punt» i la pantalla no hi afegeix res pel seu compte.
    expect(readiness.findings).toEqual([]);
    expect(screen.getByRole("group", { name: "Filtra les files" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/bloqueig/);
  });

  it("les solucions manuals van a Solucions addicionals, sense cap requisit inventat", async () => {
    solutions = [...solutions, solutionFixture({ id: "m1", requirement_id: null, source: "manual", title: "Integració amb el GIS municipal" })];
    await mount();
    const row = rowOf("Integració amb el GIS municipal");
    const card = row.closest("section, div.rounded-card") as HTMLElement;
    expect(card.textContent).toContain("Solucions addicionals");
    expect(row.textContent).not.toContain("Requisit no disponible");
  });

  it("agrupa per la categoria real del requisit", async () => {
    await mount();
    const table = screen.getAllByRole("table")[0];
    expect(table.textContent).toContain("Condicions tècniques");
    expect(table.textContent).toContain("Condicions del contracte");
  });

  it("el recompte és real: requisits, amb solució, validades", async () => {
    await mount();
    expect(document.body.textContent).toContain("2 requisits · 1 amb solució · 0 validades");
  });
});

describe("Solució tècnica · filtres", () => {
  beforeEach(() => {
    requirements = [
      requirementFixture({ id: "r1", title: "Meva" }),
      requirementFixture({ id: "r2", title: "D'altri" }),
      requirementFixture({ id: "r3", title: "Sense res" }),
      requirementFixture({ id: "r4", title: "Caducada" }),
    ];
    solutions = [
      solutionFixture({ id: "s1", requirement_id: "r1", owner_id: "u1", title: "La meva solució" }),
      solutionFixture({ id: "s2", requirement_id: "r2", owner_id: "u2", status: "needs_validation", title: "La d'en Marc" }),
      solutionFixture({ id: "s4", requirement_id: "r4", owner_id: null, status: "validated", validated_at: "2026-09-13T09:00:00Z", validation_stale: true, validation_stale_reasons: ["content_changed"], title: "La caducada" }),
    ];
    readiness = readinessWith(4);
  });

  it("Totes", async () => {
    await mount();
    expect(screen.getAllByRole("table")[0].querySelectorAll("tbody tr:not([data-expanded])").length).toBeGreaterThanOrEqual(4);
  });

  it("Em toquen a mi", async () => {
    await mount();
    await filter("Em toquen a mi");
    expect(screen.getByText("Meva")).toBeTruthy();
    expect(screen.queryByText("D'altri")).toBeNull();
    expect(screen.queryByText("Sense res")).toBeNull();
  });

  it("Sense solució", async () => {
    await mount();
    await filter("Sense solució");
    expect(screen.getByText("Sense res")).toBeTruthy();
    expect(screen.queryByText("Meva")).toBeNull();
  });

  it("Per validar: pendents i caducades", async () => {
    await mount();
    await filter("Per validar");
    expect(screen.getByText("D'altri")).toBeTruthy();
    expect(screen.getByText("Caducada")).toBeTruthy();
    expect(screen.queryByText("Meva")).toBeNull();
  });
});

/* ----- Abast --------------------------------------------------------------- */

describe("Solució tècnica · abast", () => {
  it("sense revisar", async () => {
    readiness = readinessWith(2, { scope_reviewed: false, scope_stale: false, scope_reviewed_at: null, scope_reviewed_by: null });
    await mount();
    expect(screen.getByText("Abast sense revisar")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirma l'abast" })).toBeTruthy();
  });

  it("revisat, amb qui i quan", async () => {
    readiness = readinessWith(2, { scope_reviewed: true, scope_stale: false, scope_reviewed_by: "u2" });
    await mount();
    expect(screen.getByText("Abast revisat")).toBeTruthy();
    expect(document.body.textContent).toContain("Marc Roca");
    expect(screen.getByRole("button", { name: "Torna a revisar l'abast" })).toBeTruthy();
  });

  it("caducat: el bàner aprovat, sense prometre cap diff", async () => {
    readiness = readinessWith(2, { scope_reviewed: true, scope_stale: true });
    await mount();
    expect(screen.getByText("Revisió caducada")).toBeTruthy();
    const banner = document.querySelector("[role='status'][data-level='attention']") as HTMLElement;
    expect(banner.textContent).toContain("El contingut que defineix l'abast ha canviat des de l'última revisió.");
    expect(banner.textContent).not.toMatch(/abans|ara:|afegit|eliminat/i);
  });

  it("l'abast caducat no converteix cap validació en caducada", async () => {
    readiness = readinessWith(2, { scope_reviewed: true, scope_stale: true });
    solutions = [solutionFixture({ id: "s1", requirement_id: "r1", status: "validated", validated_at: "2026-09-13T09:00:00Z", validation_stale: false })];
    await mount();
    const row = rowOf("Commutadors amb doble alimentació");
    expect(row.textContent).toContain("Validada");
    expect(row.textContent).not.toContain("Validació caducada");
  });

  it("confirmar l'abast fa el POST real, sense motiu ni modal, i invalida la preparació", async () => {
    readiness = readinessWith(2, { scope_reviewed: false });
    post.mockResolvedValue(readinessFixture());
    await mount();
    const before = getReadiness.mock.calls.length;
    await click("Confirma l'abast");
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/technical-scope/review", {});
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(getReadiness.mock.calls.length).toBe(before + 1);
  });
});

/* ----- Creació ------------------------------------------------------------- */

describe("Solució tècnica · creació", () => {
  it("«Defineix una solució» crida from-requirement només amb l'id, refresca, obre la fila i invalida", async () => {
    const created = solutionFixture({ id: "s2", requirement_id: "r2", title: "Garantia ampliada", status: "not_started", proposed_solution: null });
    post.mockImplementation(async () => {
      solutions = [...solutions, created];
      return { created: true, solution: created };
    });
    await mount();
    const before = getReadiness.mock.calls.length;
    await click("Defineix una solució", rowOf("Garantia de 36 mesos"));
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/technical-solutions/from-requirement");
    expect(body).toEqual({ requirement_id: "r2" });
    expect(body).not.toHaveProperty("source");
    expect(screen.getByLabelText(/Títol de la solució/)).toBeTruthy();
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Garantia ampliada");
    expect(getReadiness.mock.calls.length).toBe(before + 1);
  });

  it("un doble clic no crea dues vegades", async () => {
    const created = solutionFixture({ id: "s2", requirement_id: "r2" });
    post.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve({ created: true, solution: created }), 20)));
    await mount();
    const button = within(rowOf("Garantia de 36 mesos")).getByRole("button", { name: "Defineix una solució" });
    await act(async () => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("una solució manual: POST amb el títol i sense source", async () => {
    const created = solutionFixture({ id: "m1", requirement_id: null, source: "manual", title: "Formació pròpia" });
    post.mockImplementation(async () => {
      solutions = [...solutions, created];
      return created;
    });
    await mount();
    await click("Afegeix una solució");
    fireEvent.change(screen.getByLabelText(/Títol/), { target: { value: "Formació pròpia" } });
    await click("Crea-la");
    await flush();
    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/technical-solutions");
    expect(body).toEqual({ title: "Formació pròpia" });
    expect(screen.getByText("Formació pròpia").closest("tr")).toBeTruthy();
  });
});

/* ----- Edició -------------------------------------------------------------- */

describe("Solució tècnica · edició", () => {
  it("la fila s'expandeix a dins, amb el requisit i la cita literal al costat", async () => {
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    expect(screen.getByLabelText(/Proposta tècnica/)).toBeTruthy();
    expect(document.body.textContent).toContain("El plec, literal");
    expect(document.body.textContent).toContain("Els commutadors d'accés disposaran de doble alimentació redundant.");
  });

  it("només una fila oberta alhora", async () => {
    solutions = [solutionFixture({ id: "s1", requirement_id: "r1" }), solutionFixture({ id: "s2", requirement_id: "r2", title: "Garantia ampliada", status: "drafting" })];
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    await click("Continua", rowOf("Garantia de 36 mesos"));
    expect(screen.getAllByLabelText(/Títol de la solució/)).toHaveLength(1);
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Garantia ampliada");
  });

  it("escriure marca canvis sense desar i no desa res sol", async () => {
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Un altre títol" } });
    await flush();
    expect(screen.getByText("Canvis sense desar")).toBeTruthy();
    expect(patch).not.toHaveBeenCalled();
    expect(registerLeaveGuard).toHaveBeenLastCalledWith(expect.any(Function));
  });

  it("Desa: un sol PATCH amb només els camps canviats, i mai source ni validated", async () => {
    patch.mockImplementation(async (_path: string, body: Record<string, unknown>) => ({ ...solutions[0], ...body }));
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Un altre títol" } });
    fireEvent.change(screen.getByLabelText(/Proveïdor/), { target: { value: "" } });
    await click("Desa");
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    const [path, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/technical-solutions/s1");
    expect(body).toEqual({ title: "Un altre títol", supplier_name: null });
    expect(screen.getByText("Desat")).toBeTruthy();
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("Cancel·la plega sense PATCH", async () => {
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    await click("Cancel·la");
    expect(screen.queryByLabelText(/Títol de la solució/)).toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("amb canvis sense desar, obrir una altra fila demana confirmació", async () => {
    solutions = [solutionFixture({ id: "s1", requirement_id: "r1" }), solutionFixture({ id: "s2", requirement_id: "r2", title: "Garantia ampliada" })];
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Brut" } });
    await click("Continua", rowOf("Garantia de 36 mesos"));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Canvis sense desar");
    // Cancel·lar conserva el que s'estava escrivint.
    await click("Cancel·la", dialog);
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Brut");
    // Descartar obre l'altra fila.
    await click("Continua", rowOf("Garantia de 36 mesos"));
    await click("Descarta els canvis", screen.getByRole("dialog"));
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Garantia ampliada");
    expect(patch).not.toHaveBeenCalled();
  });

  it("canviar de filtre amb canvis sense desar també pregunta", async () => {
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Brut" } });
    await filter("Sense solució");
    expect(screen.getByRole("dialog").textContent).toContain("Canvis sense desar");
  });

  it("editar una solució validada no envia mai status", async () => {
    solutions = [solutionFixture({ id: "s1", requirement_id: "r1", status: "validated", validated_at: "2026-09-13T09:00:00Z" })];
    patch.mockImplementation(async (_path: string, body: Record<string, unknown>) => ({ ...solutions[0], ...body }));
    await mount();
    await click("Obre", rowOf("Commutadors amb doble alimentació"));
    expect(screen.queryByRole("combobox", { name: /^Estat/ })).toBeNull();
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Retocada" } });
    await click("Desa");
    const [, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toEqual({ title: "Retocada" });
    expect(body).not.toHaveProperty("status");
  });

  it("«Desa i envia a validar» és un sol PATCH amb status needs_validation", async () => {
    patch.mockImplementation(async (_path: string, body: Record<string, unknown>) => ({ ...solutions[0], ...body }));
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Notes/), { target: { value: "llesta" } });
    await click("Desa i envia a validar");
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    const [, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toEqual({ notes: "llesta", status: "needs_validation" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(rowOf("Commutadors amb doble alimentació").textContent).toContain("Cal validar");
  });

  it("un error en desar deixa el text escrit i ho diu", async () => {
    patch.mockRejectedValue(new ApiError(500, "internal_error", "Error intern"));
    await mount();
    await click("Continua", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Títol de la solució/), { target: { value: "Es conserva" } });
    await click("Desa");
    expect(screen.getByRole("alert").textContent).toContain("Error intern");
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Es conserva");
  });
});

/* ----- Validació ----------------------------------------------------------- */

describe("Solució tècnica · validació", () => {
  beforeEach(() => {
    solutions = [solutionFixture({ id: "s1", requirement_id: "r1", status: "needs_validation", owner_id: "u1", created_by: "u1" })];
  });

  it("validar demana un motiu: sense motiu no hi ha POST", async () => {
    await mount();
    await click("Valida-la", rowOf("Commutadors amb doble alimentació"));
    const dialog = screen.getByRole("dialog");
    expect((within(dialog).getByRole("button", { name: "Valida" }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });

  it("sense proposta el servidor ho rebutja: el modal es queda, amb frase humana i el motiu escrit", async () => {
    post.mockRejectedValue(new ApiError(422, "technical_solution_not_validatable", "No es pot validar una solucio sense proposta escrita."));
    await mount();
    await click("Valida-la", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Per què serveix/), { target: { value: "Compleix" } });
    await click("Valida", screen.getByRole("dialog"));
    await flush();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("No es pot validar una solució sense proposta escrita.");
    expect((screen.getByLabelText(/Per què serveix/) as HTMLTextAreaElement).value).toBe("Compleix");
  });

  it("validació correcta: POST amb la nota, fila validada, preparació invalidada", async () => {
    post.mockResolvedValue(solutionFixture({ id: "s1", requirement_id: "r1", status: "validated", validated_at: "2026-09-14T10:00:00Z", validated_by: "u1", validated_note: "Compleix" }));
    await mount();
    await click("Valida-la", rowOf("Commutadors amb doble alimentació"));
    fireEvent.change(screen.getByLabelText(/Per què serveix/), { target: { value: "Compleix" } });
    await click("Valida", screen.getByRole("dialog"));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/technical-solutions/s1/validation", { note: "Compleix" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(rowOf("Commutadors amb doble alimentació").textContent).toContain("Validada");
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("qui l'ha escrita també la pot validar: no s'inventa cap restricció", async () => {
    await mount();
    expect(within(rowOf("Commutadors amb doble alimentació")).getByRole("button", { name: "Valida-la" })).toBeTruthy();
  });

  it("validació caducada: «Torna a validar» i el motiu real", async () => {
    solutions = [
      solutionFixture({ id: "s1", requirement_id: "r1", status: "validated", validated_at: "2026-09-13T09:00:00Z", validation_stale: true, validation_stale_reasons: ["content_changed"] }),
      solutionFixture({ id: "s2", requirement_id: "r2", status: "validated", validated_at: "2026-09-13T09:00:00Z", validation_stale: true, validation_stale_reasons: ["requirement_changed"] }),
    ];
    await mount();
    const first = rowOf("Commutadors amb doble alimentació");
    expect(first.textContent).toContain("Validació caducada");
    expect(first.textContent).toContain("La solució ha canviat des de la validació");
    expect(within(first).getByRole("button", { name: "Torna a validar" })).toBeTruthy();
    expect(rowOf("Garantia de 36 mesos").textContent).toContain("El requisit ha canviat des de la validació");
  });

  it("requisit no disponible: es conserva, és un avís i no s'ofereix cap esborrat", async () => {
    solutions = [solutionFixture({ id: "s9", requirement_id: null, title: "Formació del personal", status: "validated", validated_at: "2026-09-13T09:00:00Z", validation_stale: true, validation_stale_reasons: ["requirement_missing"] })];
    await mount();
    const row = rowOf("Formació del personal");
    expect(row.textContent).toContain("Requisit no disponible");
    expect(row.textContent).toContain("El requisit vinculat ja no està disponible");
    expect(row.querySelectorAll("[data-level='blocking']")).toHaveLength(0);
    expect(within(row).queryByRole("button", { name: /Elimina|Arxiva/ })).toBeNull();
  });
});

describe("Solució tècnica · absent ≠ fora de l'abast", () => {
  it("fora de l'abast: secció pròpia, neutra, sense parlar de cap requisit perdut", async () => {
    requirements = [requirementFixture({ id: "r1" }), requirementFixture({ id: "l2", title: "Del lot 2", tender_lot_id: "lot-2" })];
    solutions = [solutionFixture({ id: "s2", requirement_id: "l2", title: "Solució del lot 2" })];
    readiness = readinessWith(1);
    await mount({ tender_lot_id: null });
    const row = rowOf("Solució del lot 2");
    expect(document.body.textContent).toContain("Fora de l'abast actual");
    expect(row.textContent).toContain("Del lot 2");
    expect(row.textContent).not.toContain("Requisit no disponible");
    expect(row.textContent).not.toContain("ja no està disponible");
    expect(row.querySelectorAll("[data-level='attention'], [data-level='blocking']")).toHaveLength(0);
    // No compta com a requisit de l'abast d'ara.
    expect(document.body.textContent).toContain("1 requisit · 0 amb solució");
  });

  it("absent de debò: secció pròpia, amb l'avís", async () => {
    solutions = [solutionFixture({ id: "s9", requirement_id: null, title: "Sense vincle" })];
    await mount();
    expect(document.body.textContent).toContain("El servidor no pot recuperar el requisit");
    expect(rowOf("Sense vincle").textContent).toContain("Requisit no disponible");
  });

  it("les dues seccions no existeixen quan no calen", async () => {
    await mount();
    expect(document.body.textContent).not.toContain("Fora de l'abast actual");
    expect(document.body.textContent).not.toContain("El servidor no pot recuperar el requisit");
  });
});

/* ----- Permisos i enllaços ------------------------------------------------- */

describe("Solució tècnica · permisos i enllaços", () => {
  it("consulta: cap control d'edició, cap formulari, una nota de permís", async () => {
    role = "viewer";
    await mount();
    expect(screen.queryByRole("button", { name: "Defineix una solució" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Afegeix una solució" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirma l'abast|Torna a revisar/ })).toBeNull();
    expect(screen.getAllByText(/Només qui té el rol de/)).toHaveLength(1);
    await click("Obre", rowOf("Commutadors amb doble alimentació"));
    expect(screen.queryByLabelText(/Títol de la solució/)).toBeNull();
    expect(document.body.textContent).toContain("Es desplegaran 14 commutadors amb doble font.");
  });

  it("tècnic: pot definir, editar, validar i revisar l'abast", async () => {
    role = "technical";
    await mount();
    expect(screen.getByRole("button", { name: "Defineix una solució" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Afegeix una solució" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Confirma l'abast|Torna a revisar/ })).toBeTruthy();
  });

  it("?entity= obre la solució, la fa visible i hi porta el focus", async () => {
    search = "pas=preparar&area=solucio&entity=s1";
    await mount();
    expect((screen.getByLabelText(/Títol de la solució/) as HTMLInputElement).value).toBe("Commutació d'accés amb redundància");
    const highlighted = document.querySelector("[data-highlight='true']") as HTMLElement;
    expect(highlighted).toBeTruthy();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(highlighted.contains(document.activeElement)).toBe(true);
    expect(highlighted.textContent).toContain("Solució enllaçada");
  });

  it("un problema global arriba sense entity i la pantalla s'obre normal", async () => {
    search = "pas=preparar&area=solucio";
    await mount();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByLabelText(/Títol de la solució/)).toBeNull();
  });

  it("una entity que no existeix no peta: ho diu i deixa la pantalla sencera", async () => {
    search = "pas=preparar&area=solucio&entity=no-hi-es";
    await mount();
    expect(screen.getByRole("status").textContent).toContain("no és en aquesta llista");
    expect(screen.getByText("Commutadors amb doble alimentació")).toBeTruthy();
  });
});

/* ----- Abast per lots ------------------------------------------------------ */

describe("Solució tècnica · lots", () => {
  it("candidatura d'un lot: només els generals i els del seu lot, sense llegir l'oferta", async () => {
    requirements = [requirementFixture({ id: "g", title: "General" }), requirementFixture({ id: "l1", title: "Del lot 1", tender_lot_id: "lot-1" }), requirementFixture({ id: "l2", title: "Del lot 2", tender_lot_id: "lot-2" })];
    solutions = [];
    readiness = readinessWith(2);
    await mount({ tender_lot_id: "lot-1" });
    expect(screen.getByText("General")).toBeTruthy();
    expect(screen.getByText("Del lot 1")).toBeTruthy();
    expect(screen.queryByText("Del lot 2")).toBeNull();
  });

  it("candidatura global: els lots triats a l'oferta; sense oferta, només els generals", async () => {
    requirements = [requirementFixture({ id: "g", title: "General" }), requirementFixture({ id: "l2", title: "Del lot 2", tender_lot_id: "lot-2" })];
    solutions = [];
    offerLots = [{ tender_lot_id: "lot-2", archived_at: null }];
    canSelectLots = true;
    readiness = readinessWith(2);
    const first = await mount({ tender_lot_id: null });
    expect(screen.getByText("Del lot 2")).toBeTruthy();
    first.unmount();
    offerLots = null;
    readiness = readinessWith(1);
    await mount({ tender_lot_id: null });
    expect(screen.getByText("General")).toBeTruthy();
    expect(screen.queryByText("Del lot 2")).toBeNull();
  });
});

describe("Solució tècnica · l'abast s'ha de poder representar", () => {
  it("si el recompte del servidor no quadra, no es pot confirmar i es diu", async () => {
    readiness = readinessWith(7, { scope_reviewed: false });
    await mount();
    const banner = document.querySelector("[role='status'][data-level='blocking']") as HTMLElement;
    expect(banner.textContent).toContain("No s'ha pogut representar l'abast tècnic amb seguretat");
    expect(banner.textContent).toContain("mostra 2 requisits i el servidor en compta 7");
    expect(screen.queryByRole("button", { name: /Confirma l'abast|Torna a revisar/ })).toBeNull();
    expect(within(banner).getByRole("button", { name: "Torna-ho a carregar" })).toBeTruthy();
  });

  it("si quadra, es pot confirmar", async () => {
    readiness = readinessWith(2, { scope_reviewed: false });
    await mount();
    expect(document.querySelector("[data-level='blocking']")).toBeNull();
    expect(screen.getByRole("button", { name: "Confirma l'abast" })).toBeTruthy();
  });

  it("si la preparació no es pot llegir, no s'ofereix confirmar res", async () => {
    readiness = { workspace_id: "w1" } as never;
    await mount();
    expect(screen.getByText("Abast: estat desconegut")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Confirma l'abast|Torna a revisar/ })).toBeNull();
  });
});

describe("Solució tècnica · cas C sense lots triats", () => {
  beforeEach(() => {
    requirements = [requirementFixture({ id: "g", title: "General" }), requirementFixture({ id: "l2", title: "Del lot 2", tender_lot_id: "lot-2" })];
    solutions = [];
    readiness = readinessWith(1, { scope_reviewed: false });
  });

  it("amb lots per triar i cap de triat: es veuen els generals, però no es pot confirmar", async () => {
    canSelectLots = true;
    offerLots = [];
    await mount({ tender_lot_id: null });
    expect(screen.getByText("General")).toBeTruthy();
    expect(screen.queryByText("Del lot 2")).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirma l'abast/ })).toBeNull();
    expect(document.body.textContent).toContain("Primer cal triar a quins lots es presenta la candidatura");
  });

  it("amb un lot triat sí que es pot confirmar", async () => {
    canSelectLots = true;
    offerLots = [{ tender_lot_id: "lot-2", archived_at: null }];
    readiness = readinessWith(2, { scope_reviewed: false });
    await mount({ tender_lot_id: null });
    expect(screen.getByRole("button", { name: "Confirma l'abast" })).toBeTruthy();
  });

  it("un lot arxivat no compta com a triat", async () => {
    canSelectLots = true;
    offerLots = [{ tender_lot_id: "lot-2", archived_at: "2026-09-14T09:00:00Z" }];
    await mount({ tender_lot_id: null });
    // El requisit d'aquell lot no entra a l'abast...
    expect(screen.getByText("General")).toBeTruthy();
    expect(screen.queryByText("Del lot 2")).toBeNull();
    // ...i per tant continua sense haver-hi cap lot triat.
    expect(screen.queryByRole("button", { name: /Confirma l'abast/ })).toBeNull();
    expect(document.body.textContent).toContain("Primer cal triar a quins lots");
  });

  it("sense oferta mana el servidor: si rebutja, es diu en paraules i l'abast no queda revisat", async () => {
    offerLots = null;
    post.mockRejectedValue(new ApiError(409, "technical_scope_not_confirmable", "Primer cal triar a quins lots es presenta la candidatura."));
    await mount({ tender_lot_id: null });
    await click("Confirma l'abast");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("Primer cal triar a quins lots es presenta la candidatura.");
    expect(screen.getByText("Abast sense revisar")).toBeTruthy();
    expect(getReadiness).toHaveBeenCalledTimes(1);
  });
});
