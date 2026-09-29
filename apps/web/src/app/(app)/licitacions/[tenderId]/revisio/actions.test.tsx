import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { CanonicalRequirement, ReviewHistoryEntry } from "@/lib/review";
import { awardAll, awardScoring, reviewOverview, reviewRequirements } from "@/test/review-fixture";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) },
  };
});
let role = "technical";
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    get membership() {
      return { id: "m1", company_id: "c1", role, is_default: true };
    },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ tenderId: "t1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/licitacions/t1/revisio",
  useSearchParams: () => new URLSearchParams(""),
}));

import TenderReviewPage from "./page";

const REVIEW = "/companies/c1/tenders/t1/review";
const SOLVENCY = reviewRequirements.find((item) => item.canonical_id === "r-solvency") as CanonicalRequirement;
const M15 = reviewRequirements[0];

let routes: Record<string, unknown>;

function respond(overrides: Record<string, unknown> = {}) {
  routes = {
    "/tenders/t1": { title: "Palamós" },
    [`${REVIEW}/overview`]: reviewOverview,
    [`${REVIEW}/requirements`]: reviewRequirements,
    [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
    [`${REVIEW}/award-criteria?view=all`]: awardAll,
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    if (path in routes) {
      const value = routes[path];
      return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
    }
    if (path.endsWith("/history")) return Promise.resolve([]);
    return Promise.reject(new ApiError(404, "not_found", "No trobat"));
  });
}

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function openItem(title: RegExp) {
  render(<TenderReviewPage />);
  await flush();
  await flush();
  fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: title }));
  await flush();
  return screen.getByRole("dialog");
}

function calls(path: string) {
  return get.mock.calls.filter(([called]) => called === path).length;
}

function after(item: CanonicalRequirement, status: string, label: string, revision = item.review_revision + 1): CanonicalRequirement {
  return { ...item, verification_status: status, verification_label: label, review_revision: revision };
}

async function click(name: string | RegExp, scope: HTMLElement = screen.getByRole("dialog")) {
  fireEvent.click(within(scope).getByRole("button", { name }));
  await flush();
}

function typeInto(dialog: HTMLElement, text: string) {
  fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: text } });
}

describe("Accions de revisió d'un requisit", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    role = "technical";
    respond();
  });

  it("Confirmar envia la revisió visible i mostra el que torna el servidor", async () => {
    post.mockResolvedValue(after(SOLVENCY, "verified", "Verificat"));
    const dialog = await openItem(/Volum anual de negocis mínim/);
    const revision = within(dialog).getByText("Revisió", { selector: "dt" }).nextElementSibling;
    expect(revision?.textContent).toBe("1");

    await click("Confirmar requisit", dialog);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${REVIEW}/items/r-solvency/actions`, {
      action: "confirm",
      expected_revision: 1,
      payload: {},
    });
    expect(within(dialog).getByText("Requisit confirmat.")).toBeTruthy();
    expect(revision?.textContent).toBe("2");
    const row = within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ });
    expect(within(row).getByText("Verificat")).toBeTruthy();
    // El resum es torna a llegir: cap recompte fet a mà.
    expect(calls(`${REVIEW}/overview`)).toBe(2);
  });

  it("confirmar un requisit manual no en canvia l'origen", async () => {
    post.mockResolvedValue(after(M15, "verified", "Verificat"));
    const dialog = await openItem(/Mínim 64 trucades simultànies/);

    await click("Confirmar requisit", dialog);

    const row = within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Mínim 64 trucades/ });
    expect(within(row).getByText("Afegit manualment")).toBeTruthy();
    expect(within(row).getByText("Verificat")).toBeTruthy();
    // L'origen continua sent «Afegit manualment» (i el valor també ho diu).
    const origin = within(dialog).getByText("Origen").closest("div")!;
    expect(within(origin).getByText("Afegit manualment")).toBeTruthy();
  });

  it("Marcar parcial demana una nota abans d'enviar res", async () => {
    post.mockResolvedValue(after(SOLVENCY, "partial", "Parcial"));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Marcar parcial", dialog);
    await click("Marca parcial", dialog);
    expect(within(dialog).getByText("Cal explicar què és parcial.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();

    typeInto(dialog, "  Només cobreix el darrer exercici  ");
    await click("Marca parcial", dialog);

    expect(post).toHaveBeenCalledWith(`${REVIEW}/items/r-solvency/actions`, {
      action: "mark_partial",
      expected_revision: 1,
      payload: { note: "Només cobreix el darrer exercici" },
    });
    expect(within(dialog).getByText("Requisit marcat com a parcial.")).toBeTruthy();
    expect(within(dialog).getAllByText("Parcial").length).toBeGreaterThan(0);
  });

  it("Descartar demana un motiu i diu que no s'esborra res", async () => {
    post.mockResolvedValue(after(SOLVENCY, "rejected", "Descartat"));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Descartar", dialog);
    expect(within(dialog).getByText(/No s'esborra res/)).toBeTruthy();
    expect(within(dialog).queryByText(/Elimina|Esborra/)).toBeNull();
    await click("Descarta de la revisió", dialog);
    expect(within(dialog).getByText("Cal indicar el motiu.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();

    typeInto(dialog, "No aplica a aquest lot");
    await click("Descarta de la revisió", dialog);

    expect(post.mock.calls[0][1]).toEqual({ action: "reject", expected_revision: 1, payload: { reason: "No aplica a aquest lot" } });
    expect(within(dialog).getByText(/L'extracció es conserva/)).toBeTruthy();
  });

  it("Afegir nota no canvia l'estat ni torna a llegir el resum", async () => {
    post.mockResolvedValue({ ...SOLVENCY, review_revision: 2 });
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Afegir nota", dialog);
    expect(within(dialog).getByText(/No canvia l'estat del requisit ni el dona per verificat/)).toBeTruthy();
    typeInto(dialog, "Cal confirmar-ho amb l'òrgan");
    await click("Desa la nota", dialog);

    expect(post.mock.calls[0][1]).toEqual({ action: "note", expected_revision: 1, payload: { note: "Cal confirmar-ho amb l'òrgan" } });
    expect(within(dialog).getByText("Nota afegida a l'historial.")).toBeTruthy();
    expect(within(dialog).getByText("Revisió", { selector: "dt" }).nextElementSibling?.textContent).toBe("2");
    expect(within(dialog).getAllByText("Pendent de revisar").length).toBeGreaterThan(0);
    expect(calls(`${REVIEW}/overview`)).toBe(1);
  });

  it("mentre s'envia, no es pot tornar a enviar ni marcar res abans d'hora", async () => {
    let resolve: (value: CanonicalRequirement) => void = () => {};
    post.mockImplementation(() => new Promise((done) => (resolve = done)));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Confirmar requisit", dialog);
    const confirm = within(dialog).getByRole("button", { name: /Confirmar/ }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Descartar" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirm);
    expect(post).toHaveBeenCalledTimes(1);
    // Encara no: l'estat no canvia fins que respon el servidor.
    expect(within(dialog).queryByText("Verificat")).toBeNull();

    await act(async () => resolve(after(SOLVENCY, "verified", "Verificat")));
    await flush();
    expect(within(dialog).getAllByText("Verificat").length).toBeGreaterThan(0);
  });

  it("409: no reintenta, avisa i mostra la versió actual", async () => {
    post.mockRejectedValue(new ApiError(409, "review_revision_conflict", "Algu altre ha revisat aquest element."));
    const current = after(SOLVENCY, "verified", "Verificat", 3);
    routes[`${REVIEW}/requirements/r-solvency`] = current;
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Marcar parcial", dialog);
    typeInto(dialog, "Una part no hi és");
    await click("Marca parcial", dialog);

    expect(post).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("alert").textContent).toMatch(/ha canviat des que l'has obert/);
    expect(calls(`${REVIEW}/requirements/r-solvency`)).toBe(1);
    expect(within(dialog).getByText("Revisió", { selector: "dt" }).nextElementSibling?.textContent).toBe("3");
    const row = within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ });
    expect(within(row).getByText("Verificat")).toBeTruthy();
    // El formulari es tanca: cal tornar-ho a decidir sobre la versió nova.
    expect(within(dialog).getByRole("button", { name: "Marcar parcial" })).toBeTruthy();
    expect(calls(`${REVIEW}/overview`)).toBe(2);
  });

  it.each([
    [403, "forbidden", "No tens permisos per modificar aquesta revisió."],
    [404, "not_found", "L'element ja no està disponible."],
    [500, "error", "No s'ha pogut desar. Torna-ho a provar d'aquí a un moment."],
  ])("error %i: una frase clara, cap codi", async (status, code, message) => {
    post.mockRejectedValue(new ApiError(status, code, "detall intern"));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Confirmar requisit", dialog);

    expect(within(dialog).getByRole("alert").textContent).toBe(message);
    expect(document.body.textContent).not.toContain("detall intern");
    expect(document.body.textContent).not.toContain(code === "error" ? "Error 500" : code);
    if (status === 404) expect(calls(`${REVIEW}/requirements`)).toBe(2);
  });

  it("un error de xarxa també es pot tornar a provar", async () => {
    post.mockRejectedValue(new TypeError("Failed to fetch"));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Confirmar requisit", dialog);

    expect(within(dialog).getByRole("alert").textContent).toMatch(/Torna-ho a provar/);
    expect((within(dialog).getByRole("button", { name: "Confirmar requisit" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("422: el missatge del backend al costat del formulari", async () => {
    post.mockRejectedValue(new ApiError(422, "validation_error", "La carrega de l'accio no es valida."));
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Afegir nota", dialog);
    typeInto(dialog, "x");
    await click("Desa la nota", dialog);

    const field = within(dialog).getByRole("textbox");
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(within(dialog).getByText("La carrega de l'accio no es valida.")).toBeTruthy();
    expect(within(dialog).queryByRole("alert")).toBeNull();
  });

  it("els filtres es mantenen i l'element surt si ja no hi coincideix", async () => {
    post.mockResolvedValue(after(SOLVENCY, "verified", "Verificat"));
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.change(screen.getByLabelText("Estat"), { target: { value: "unreviewed" } });
    const list = () => screen.getByRole("list", { name: "Requisits" });
    const before = within(list()).getAllByRole("listitem").length;
    fireEvent.click(within(list()).getByRole("button", { name: /Volum anual/ }));
    await flush();

    await click("Confirmar requisit");

    expect((screen.getByLabelText("Estat") as HTMLSelectElement).value).toBe("unreviewed");
    expect(within(list()).getAllByRole("listitem")).toHaveLength(before - 1);
    expect(within(list()).queryByRole("button", { name: /Volum anual/ })).toBeNull();
    // El detall continua obert, amb l'estat nou.
    expect(within(screen.getByRole("dialog")).getAllByText("Verificat").length).toBeGreaterThan(0);
  });

  it("l'historial ensenya les tres últimes i es pot desplegar", async () => {
    const entries: ReviewHistoryEntry[] = [
      { revision_no: 1, action: "create", action_label: "Creat", status_after: "unreviewed", status_label: "Pendent de revisar", note: null, actor_id: "x", created_at: "2026-09-27T20:24:09Z" },
      { revision_no: 2, action: "note", action_label: "Nota", status_after: "unreviewed", status_label: "Pendent de revisar", note: "Primer comentari", actor_id: "u1", created_at: "2026-09-28T10:00:00Z" },
      { revision_no: 3, action: "confirm", action_label: "Confirmat", status_after: "verified", status_label: "Verificat", note: null, actor_id: "u1", created_at: "2026-09-28T11:00:00Z" },
      { revision_no: 4, action: "mark_partial", action_label: "Marcat com a parcial", status_after: "partial", status_label: "Parcial", note: "Falta un exercici", actor_id: "u2", created_at: "2026-09-28T12:00:00Z" },
    ];
    routes[`${REVIEW}/items/r-solvency/history`] = entries;
    const dialog = await openItem(/Volum anual de negocis mínim/);

    const history = within(dialog).getByRole("heading", { name: "Historial de revisió" }).parentElement as HTMLElement;
    const items = () => within(history).getAllByRole("listitem");
    expect(items()).toHaveLength(3);
    expect(items()[0].textContent).toMatch(/Marcat com a parcial → Parcial/);
    expect(items()[0].textContent).toMatch(/Falta un exercici/);
    expect(items()[1].textContent).toMatch(/· tu/);
    expect(items()[0].textContent).not.toMatch(/· tu/);
    await click("Veure tot l'historial (4)", history);
    expect(items()).toHaveLength(4);
    expect(items()[3].textContent).toMatch(/Creat → Pendent de revisar/);
    expect(history.textContent).not.toMatch(/payload|\{/);
  });

  it("l'historial es torna a llegir després d'una acció", async () => {
    post.mockResolvedValue(after(SOLVENCY, "verified", "Verificat"));
    const dialog = await openItem(/Volum anual de negocis mínim/);
    expect(calls(`${REVIEW}/items/r-solvency/history`)).toBe(1);

    await click("Confirmar requisit", dialog);

    expect(calls(`${REVIEW}/items/r-solvency/history`)).toBe(2);
  });

  it("un rol de consulta no veu cap acció d'escriptura", async () => {
    role = "viewer";
    const dialog = await openItem(/Volum anual de negocis mínim/);

    const section = within(dialog).getByRole("heading", { name: "Revisió" }).closest("section") as HTMLElement;
    expect(within(section).queryByRole("button")).toBeNull();
    expect(section.textContent).toMatch(/Només qui té el rol de .* pot revisar requisits/);
    expect(within(section).getByText("Pendent de revisar")).toBeTruthy();
  });

  it("Escape tanca el formulari però no el detall, i el focus torna al botó", async () => {
    const dialog = await openItem(/Volum anual de negocis mínim/);

    await click("Afegir nota", dialog);
    const field = within(dialog).getByRole("textbox");
    expect(document.activeElement).toBe(field);
    fireEvent.keyDown(field, { key: "Escape" });
    await flush();

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dialog).queryByRole("textbox")).toBeNull();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Afegir nota" }));
  });
});
