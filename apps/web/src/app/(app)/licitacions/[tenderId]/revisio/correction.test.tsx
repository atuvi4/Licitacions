import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { CanonicalRequirement } from "@/lib/review";
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

async function openEditor(title: RegExp) {
  render(<TenderReviewPage />);
  await flush();
  await flush();
  fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: title }));
  await flush();
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Corregir" }));
  await flush();
  return { dialog, editor: within(dialog).getByRole("group", { name: "Corregir el requisit" }) };
}

const field = (scope: HTMLElement, label: string) => within(scope).getByLabelText(label, { exact: false }) as HTMLInputElement;
const save = (editor: HTMLElement) => within(editor).getByRole("button", { name: /Desa la correcció/ }) as HTMLButtonElement;

async function submit(editor: HTMLElement) {
  fireEvent.click(save(editor));
  await flush();
  await flush();
}

function payload(call = 0) {
  return post.mock.calls[call][1] as { action: string; expected_revision: number; payload: Record<string, unknown> };
}

function corrected(item: CanonicalRequirement, patch: Partial<CanonicalRequirement>): CanonicalRequirement {
  return { ...item, provenance: "automated_corrected", provenance_label: "Extret i corregit", review_revision: item.review_revision + 1, ...patch };
}

describe("Editor de correccions", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    role = "technical";
    respond();
  });

  it("s'obre amb la instantània acceptada, sense codis a la vista", async () => {
    const { editor } = await openEditor(/Volum anual de negocis mínim/);

    expect(field(editor, "Títol").value).toBe("Volum anual de negocis mínim");
    expect((field(editor, "Categoria") as unknown as HTMLSelectElement).value).toBe("economic_solvency");
    expect((field(editor, "Modalitat") as unknown as HTMLSelectElement).value).toBe("conditional");
    const value = within(editor).getByRole("group", { name: "Valor 1" });
    expect((within(value).getByLabelText("Valor") as HTMLInputElement).value).toBe("450000");
    expect((within(value).getByLabelText("Unitat") as HTMLInputElement).value).toBe("EUR");
    const operator = within(value).getByLabelText("Operador") as HTMLSelectElement;
    expect(operator.options[operator.selectedIndex].text).toBe("≥");
    expect(editor.textContent).not.toMatch(/economic_solvency|conditional|volum_negoci|gte/);
    // Res per desar mentre no es toca res.
    expect(save(editor).disabled).toBe(true);
    expect(within(editor).getByText("No hi ha cap canvi per desar.")).toBeTruthy();
    expect(within(editor).getByText("Corregir el contingut no implica verificar-lo.")).toBeTruthy();
  });

  it("una correcció de títol envia només el títol i l'estat actual, i el servidor mana", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, { title: "Volum anual de negoci mínim (3 exercicis)" }));
    const { dialog, editor } = await openEditor(/Volum anual de negocis mínim/);

    fireEvent.change(field(editor, "Títol"), { target: { value: "Volum anual de negoci mínim (3 exercicis)" } });
    await submit(editor);

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe(`${REVIEW}/items/r-solvency/actions`);
    expect(payload()).toEqual({
      action: "correct",
      expected_revision: 1,
      payload: { title: "Volum anual de negoci mínim (3 exercicis)", verification_status: "unreviewed" },
    });
    expect(within(dialog).getByText("Correcció desada.")).toBeTruthy();
    const row = within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /3 exercicis/ });
    expect(within(row).getByText("Extret i corregit")).toBeTruthy();
    expect(get.mock.calls.filter(([p]) => p === `${REVIEW}/overview`)).toHaveLength(2);
    expect(get.mock.calls.filter(([p]) => p === `${REVIEW}/items/r-solvency/history`)).toHaveLength(2);
  });

  it("descripció, categoria, obligació i modalitat viatgen cadascuna amb el seu codi", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, {}));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);

    fireEvent.change(field(editor, "Descripció"), { target: { value: "Dels tres darrers exercicis." } });
    fireEvent.change(field(editor, "Categoria"), { target: { value: "technical_solvency" } });
    fireEvent.change(field(editor, "Obligació"), { target: { value: "scored" } });
    fireEvent.change(field(editor, "Modalitat"), { target: { value: "mandatory" } });
    await submit(editor);

    expect(payload().payload).toEqual({
      description: "Dels tres darrers exercicis.",
      category: "technical_solvency",
      obligation: "scored",
      modality: "mandatory",
      verification_status: "unreviewed",
    });
  });

  it("editar un valor conserva la clau i el valor intacte no s'envia", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, {}));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);

    fireEvent.click(within(editor).getByRole("button", { name: "Afegeix un valor" }));
    const fresh = within(editor).getByRole("group", { name: "Valor 2" });
    fireEvent.change(within(fresh).getByLabelText("Operador"), { target: { value: "<" } });
    fireEvent.change(within(fresh).getByLabelText("Valor"), { target: { value: "70" } });
    fireEvent.change(within(fresh).getByLabelText("Unitat"), { target: { value: "ms" } });
    await submit(editor);

    // El del document (450.000 EUR) no s'ha tocat: no viatja i continua sent del document.
    expect(payload().payload).toEqual({
      values: [{ key: "reviewer_value_1", operator: "<", value: 70, unit_label: "ms" }],
      verification_status: "unreviewed",
    });
  });

  it("canviar un valor existent n'envia la mateixa clau; treure'l va a remove_values", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, {}));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    const row = within(editor).getByRole("group", { name: "Valor 1" });

    fireEvent.change(within(row).getByLabelText("Valor"), { target: { value: "500000" } });
    await submit(editor);
    expect(payload().payload.values).toEqual([{ key: "volum_negoci", operator: "gte", value: 500000, unit_label: "EUR" }]);

    post.mockClear();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Corregir" }));
    await flush();
    const again = within(screen.getByRole("dialog")).getByRole("group", { name: "Corregir el requisit" });
    fireEvent.click(within(again).getByRole("button", { name: "Treu el valor 1" }));
    await submit(again);
    expect(payload().payload).toEqual({ remove_values: ["volum_negoci"], verification_status: "unreviewed" });
  });

  it("text, sí/no i unitats lliures; una unitat de 33 caràcters no s'envia", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, {}));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.click(within(editor).getByRole("button", { name: "Afegeix un valor" }));
    fireEvent.click(within(editor).getByRole("button", { name: "Afegeix un valor" }));
    const text = within(editor).getByRole("group", { name: "Valor 2" });
    const bool = within(editor).getByRole("group", { name: "Valor 3" });
    fireEvent.change(within(text).getByLabelText("Tipus"), { target: { value: "text" } });
    fireEvent.change(within(text).getByLabelText("Valor"), { target: { value: "70" } });
    fireEvent.change(within(text).getByLabelText("Unitat"), { target: { value: "u".repeat(33) } });
    fireEvent.change(within(bool).getByLabelText("Tipus"), { target: { value: "boolean" } });
    fireEvent.change(within(bool).getByLabelText("Valor"), { target: { value: "false" } });

    await submit(editor);
    expect(post).not.toHaveBeenCalled();
    expect(within(text).getByText(/com a molt 32 caràcters/)).toBeTruthy();

    fireEvent.change(within(text).getByLabelText("Unitat"), { target: { value: "u".repeat(32) } });
    await submit(editor);
    expect(payload().payload.values).toEqual([
      { key: "reviewer_value_1", operator: null, value: "70", unit_label: "u".repeat(32) },
      { key: "reviewer_value_2", operator: null, value: false, unit_label: null },
    ]);
  });

  it("un número no vàlid no s'envia", async () => {
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    const row = within(editor).getByRole("group", { name: "Valor 1" });
    fireEvent.change(within(row).getByLabelText("Valor"), { target: { value: "Infinity" } });

    await submit(editor);

    expect(post).not.toHaveBeenCalled();
    expect(within(row).getByText(/Escriu un número/)).toBeTruthy();
  });

  it("es pot triar Verificat explícitament", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, { verification_status: "verified", verification_label: "Verificat" }));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "Volum de negoci" } });
    fireEvent.click(within(editor).getByLabelText(/Verificat: he comprovat/));

    await submit(editor);

    expect(payload().payload.verification_status).toBe("verified");
  });

  it("un requisit manual continua sent manual després de corregir-lo", async () => {
    post.mockResolvedValue({ ...M15, title: "Mínim 64 trucades simultànies (SIP)", review_revision: 2 });
    const { editor } = await openEditor(/Mínim 64 trucades simultànies/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "Mínim 64 trucades simultànies (SIP)" } });

    await submit(editor);

    const row = within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /\(SIP\)/ });
    expect(within(row).getByText("Afegit manualment")).toBeTruthy();
  });

  it("els filtres es mantenen i l'element surt si ja no hi coincideix", async () => {
    post.mockResolvedValue(corrected(SOLVENCY, { category: "technical_solvency", category_label: "Solvència tècnica" }));
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.change(screen.getByLabelText("Categoria"), { target: { value: "economic_solvency" } });
    fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ }));
    await flush();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Corregir" }));
    await flush();
    const editor = within(screen.getByRole("dialog")).getByRole("group", { name: "Corregir el requisit" });
    fireEvent.change(field(editor, "Categoria"), { target: { value: "technical_solvency" } });

    await submit(editor);

    const filter = screen.getAllByLabelText("Categoria")[0] as HTMLSelectElement;
    expect(filter.value).toBe("economic_solvency");
    // El desplegable continua dient quin filtre hi ha, encara que ja no quedi cap element.
    expect(filter.options[filter.selectedIndex].text).toBe("Solvència econòmica (0)");
    expect(screen.getByText("Cap requisit no coincideix amb aquests filtres.")).toBeTruthy();
  });

  it("un rol de consulta no veu «Corregir»", async () => {
    role = "viewer";
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ }));
    await flush();

    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Corregir" })).toBeNull();
  });

  it("mentre desa, no es pot tornar a enviar i l'esborrany es veu", async () => {
    let resolve: (value: CanonicalRequirement) => void = () => {};
    post.mockImplementation(() => new Promise((done) => (resolve = done)));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "Títol nou" } });

    await submit(editor);
    expect(save(editor).disabled).toBe(true);
    fireEvent.click(save(editor));
    expect(post).toHaveBeenCalledTimes(1);
    expect(field(editor, "Títol").value).toBe("Títol nou");

    await act(async () => resolve(corrected(SOLVENCY, { title: "Títol nou" })));
    await flush();
  });

  it("422: missatge segur i l'esborrany es queda", async () => {
    post.mockRejectedValue(
      new ApiError(422, "validation_error", "La carrega de l'accio no es valida.", [
        { field: "values.0.unit_label", type: "string_too_long", message: "String should have at most 32 characters" },
      ]),
    );
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "Títol nou" } });

    await submit(editor);

    const alert = within(editor).getByRole("alert");
    expect(alert.textContent).toBe("La carrega de l'accio no es valida. (Valors)");
    expect(alert.textContent).not.toMatch(/String should|values\.0/);
    expect(field(editor, "Títol").value).toBe("Títol nou");
  });

  it("una fallada de xarxa conserva l'esborrany i es pot tornar a provar", async () => {
    post.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "Títol nou" } });

    await submit(editor);

    expect(within(editor).getByRole("alert").textContent).toMatch(/Torna-ho a provar/);
    expect(field(editor, "Títol").value).toBe("Títol nou");
    expect(save(editor).disabled).toBe(false);
  });

  it("Cancel·la, Escape i tancar el calaix no llencen un esborrany sense preguntar", async () => {
    const { dialog, editor } = await openEditor(/Volum anual de negocis mínim/);
    // Sense canvis: es tanca sense preguntar.
    fireEvent.click(within(editor).getByRole("button", { name: "Cancel·la" }));
    await flush();
    expect(within(dialog).queryByRole("group", { name: "Corregir el requisit" })).toBeNull();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Corregir" }));

    fireEvent.click(within(dialog).getByRole("button", { name: "Corregir" }));
    await flush();
    const dirty = within(dialog).getByRole("group", { name: "Corregir el requisit" });
    fireEvent.change(field(dirty, "Títol"), { target: { value: "Canvi a mig fer" } });

    fireEvent.click(within(dirty).getByRole("button", { name: "Cancel·la" }));
    await flush();
    expect(within(dirty).getByText("Descartar els canvis no desats?")).toBeTruthy();
    fireEvent.click(within(dirty).getByRole("button", { name: "Continua editant" }));

    fireEvent.keyDown(field(dirty, "Títol"), { key: "Escape" });
    await flush();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dirty).getByText("Descartar els canvis no desats?")).toBeTruthy();
    fireEvent.click(within(dirty).getByRole("button", { name: "Continua editant" }));

    // La creu del calaix tampoc no el tanca amb canvis pendents.
    fireEvent.click(within(dialog).getByRole("button", { name: "Tanca" }));
    await flush();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(field(dirty, "Títol").value).toBe("Canvi a mig fer");

    fireEvent.click(within(dirty).getByRole("button", { name: "Descarta els canvis" }));
    await flush();
    expect(within(dialog).queryByRole("group", { name: "Corregir el requisit" })).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("409: l'esborrany es queda, es llegeix la versió nova i cal revisar-la abans de desar", async () => {
    post.mockRejectedValueOnce(new ApiError(409, "review_revision_conflict", "Algu altre ha revisat aquest element."));
    const newer = {
      ...SOLVENCY,
      review_revision: 2,
      verification_status: "verified",
      verification_label: "Verificat",
      editable: { ...SOLVENCY.editable, description: "Descripció nova del servidor" },
    };
    routes[`${REVIEW}/requirements/r-solvency`] = newer;
    const { editor } = await openEditor(/Volum anual de negocis mínim/);
    fireEvent.change(field(editor, "Títol"), { target: { value: "El meu títol" } });

    await submit(editor);

    expect(post).toHaveBeenCalledTimes(1);
    expect(payload().expected_revision).toBe(1);
    const alert = within(editor).getByRole("alert");
    expect(alert.textContent).toMatch(/ha canviat mentre l'editaves\. No hem desat la teva correcció/);
    expect(alert.textContent).toMatch(/revisió 2/);
    expect(alert.textContent).toMatch(/Ha canviat: Descripció/);
    expect(field(editor, "Títol").value).toBe("El meu títol");
    // No es torna a enviar sol, i no es pot desar fins revisar la versió nova.
    expect(save(editor).disabled).toBe(true);

    fireEvent.click(within(editor).getByRole("button", { name: "Revisa la versió nova" }));
    await flush();
    expect(field(editor, "Descripció").value).toBe("Descripció nova del servidor");
    expect(field(editor, "Títol").value).toBe("El meu títol");
    expect(save(editor).disabled).toBe(false);

    post.mockResolvedValueOnce(corrected(newer, { title: "El meu títol" }));
    await submit(editor);
    expect(post).toHaveBeenCalledTimes(2);
    expect(payload(1)).toEqual({
      action: "correct",
      expected_revision: 2,
      payload: { title: "El meu títol", verification_status: "verified" },
    });
  });

  it("l'historial resumeix una correcció sense valors ni claus", async () => {
    routes[`${REVIEW}/items/r-solvency/history`] = [
      {
        revision_no: 2,
        action: "correct",
        action_label: "Corregit",
        status_after: "unreviewed",
        status_label: "Pendent de revisar",
        note: "Segons la clàusula 9",
        actor_id: "u1",
        created_at: "2026-09-28T12:00:00Z",
        changes: { fields: ["title"], values_set: ["reviewer_value_1"], values_removed: [], status_before: "unreviewed" },
      },
    ];
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ }));
    await flush();

    const history = within(screen.getByRole("dialog")).getByRole("heading", { name: "Historial de revisió" }).parentElement as HTMLElement;
    expect(history.textContent).toMatch(/Corregit → Pendent de revisar/);
    expect(history.textContent).toMatch(/Canvis: Títol · 1 valor/);
    expect(history.textContent).not.toMatch(/reviewer_value_1|status_before/);
  });
});
