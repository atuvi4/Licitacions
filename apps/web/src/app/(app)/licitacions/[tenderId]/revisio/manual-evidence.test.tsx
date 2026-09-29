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
const SENTENCE = "hauran de disposar de connectivitat a nivell 2 amb el datacenter\non es gestionin les trucades";
/** SINTÈTIC, amb forma de la p. 22 del PPT; l'emoji prova que les posicions són les del servidor. */
const PAGE_22 = `8.1.1.4 Connectivitat 📶\nEls equipaments municipals ${SENTENCE}.\n`;
const PAGES = {
  items: [
    { id: "page-21", page_number: 21, text: "Pàgina 21.", char_count: 10, text_hash: "h21", status: "extracted" },
    { id: "page-22", page_number: 22, text: PAGE_22, char_count: 0, text_hash: "h22", status: "extracted" },
  ],
  total: 2,
  limit: 100,
  offset: 0,
};
const DOCUMENTS = [
  {
    id: "d-ppt",
    name: "PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf",
    latest_file: { version_number: 1, analysis_status: "extracted", extraction_id: "x-ppt" },
  },
  { id: "d-xlsx", name: "Annex preus.xlsx", latest_file: { version_number: 1, analysis_status: "not_extractable", extraction_id: null } },
];
const SOLVENCY = reviewRequirements.find((item) => item.canonical_id === "r-solvency") as CanonicalRequirement;
let routes: Record<string, unknown>;

function respond(overrides: Record<string, unknown> = {}) {
  routes = {
    "/tenders/t1": { title: "Palamós", documents: DOCUMENTS },
    [`${REVIEW}/overview`]: reviewOverview,
    [`${REVIEW}/requirements`]: reviewRequirements,
    [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
    [`${REVIEW}/award-criteria?view=all`]: awardAll,
    "/companies/c1/tender-document-extractions/x-ppt/pages?limit=100&offset=0": PAGES,
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

async function renderPage() {
  render(<TenderReviewPage />);
  await flush();
  await flush();
}

const calls = (path: string) => get.mock.calls.filter(([p]) => p === path).length;

/** Tria PPT → p. 22 al selector de cites dins de `scope`. */
async function pickPage(scope: HTMLElement) {
  fireEvent.change(within(scope).getByLabelText("Document"), { target: { value: "d-ppt" } });
  await flush();
  await flush();
  fireEvent.change(within(scope).getByLabelText("Pàgina"), { target: { value: "page-22" } });
  await flush();
  return within(scope).getByLabelText("Text de la pàgina 22");
}

/** Una selecció real del DOM sobre el text de la pàgina (posicions UTF-16). */
function select(pre: HTMLElement, from: number, to: number) {
  const node = pre.firstChild as Text;
  const range = document.createRange();
  range.setStart(node, from);
  range.setEnd(node, to);
  const selection = window.getSelection() as Selection;
  selection.removeAllRanges();
  selection.addRange(range);
}

/** Posicions que tallaria Python (punts de codi). */
function pythonSpan(text: string, fragment: string) {
  const utf16 = text.indexOf(fragment);
  const start = Array.from(text.slice(0, utf16)).length;
  return { start, end: start + Array.from(fragment).length };
}

describe("Requisit manual", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    role = "technical";
    respond();
  });

  it("un rol de consulta no veu «Afegir requisit» ni «Afegir evidència»", async () => {
    role = "viewer";
    await renderPage();
    expect(screen.queryByRole("button", { name: "Afegir requisit" })).toBeNull();
    fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ }));
    await flush();
    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Afegir evidència" })).toBeNull();
  });

  it("es crea sense cita, neix pendent i manual, i es torna a llegir tot", async () => {
    const created = {
      ...reviewRequirements[0],
      canonical_id: "new-1",
      title: "[QA] Requisit manual de prova",
      evidence: [],
      evidence_label: "Sense cita",
      values: [],
    };
    post.mockResolvedValue(created);
    await renderPage();
    fireEvent.change(screen.getByLabelText("Origen"), { target: { value: "manual" } });
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });

    fireEvent.change(within(form).getByLabelText("Títol", { exact: false }), { target: { value: "  [QA] Requisit manual de prova " } });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix el requisit" }));
    await flush();
    expect(post).not.toHaveBeenCalled();
    expect(within(form).getByText("Tria una categoria.")).toBeTruthy();

    fireEvent.change(within(form).getByLabelText("Categoria", { exact: false }), { target: { value: "technical_requirement" } });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix un valor" }));
    const value = within(form).getByRole("group", { name: "Valor 1" });
    fireEvent.change(within(value).getByLabelText("Operador"), { target: { value: ">=" } });
    fireEvent.change(within(value).getByLabelText("Valor"), { target: { value: "1" } });
    fireEvent.change(within(value).getByLabelText("Unitat"), { target: { value: "prova" } });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix el requisit" }));
    await flush();
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0]).toEqual([
      `${REVIEW}/requirements`,
      {
        title: "[QA] Requisit manual de prova",
        description: null,
        category: "technical_requirement",
        obligation: "mandatory",
        modality: "mandatory",
        values: [{ key: "reviewer_value_1", operator: ">=", value: 1, unit_label: "prova" }],
        verification_status: "unreviewed",
        citations: [],
      },
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("S'ha afegit «[QA] Requisit manual de prova». Queda pendent de revisar.")).toBeTruthy();
    // Autoritat del servidor: llista i resum es tornen a llegir; el filtre es manté.
    expect(calls(`${REVIEW}/requirements`)).toBe(2);
    expect(calls(`${REVIEW}/overview`)).toBe(2);
    expect((screen.getByLabelText("Origen") as HTMLSelectElement).value).toBe("manual");
  });

  it("amb una cita triada al selector: posicions exactes, sense text enviat", async () => {
    post.mockResolvedValue({ ...reviewRequirements[0], canonical_id: "new-2" });
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });
    fireEvent.change(within(form).getByLabelText("Títol", { exact: false }), { target: { value: "Connectivitat nivell 2" } });
    fireEvent.change(within(form).getByLabelText("Categoria", { exact: false }), { target: { value: "technical_requirement" } });
    expect(within(form).getByText("Sense cita. Es pot afegir ara o més endavant.")).toBeTruthy();

    fireEvent.click(within(form).getByRole("button", { name: "Afegeix una cita" }));
    await flush();
    const documentSelect = within(form).getByLabelText("Document") as HTMLSelectElement;
    // Només els documents amb text; cap identificador a la vista.
    expect([...documentSelect.options].map((o) => o.text)).toEqual(["Tria un document", "PPT · v1"]);
    const pre = await pickPage(form);
    const from = PAGE_22.indexOf("hauran");
    select(pre, from, from + SENTENCE.length);
    fireEvent.click(within(form).getByRole("button", { name: "Usa el text seleccionat" }));
    await flush();

    expect(within(form).getByText(/«hauran de disposar de connectivitat a nivell 2 amb el datacenter on es gestionin/)).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix el requisit" }));
    await flush();

    const { start, end } = pythonSpan(PAGE_22, SENTENCE);
    expect(start).toBe(from - 1); // l'emoji compta com a un sol caràcter per al servidor
    const body = post.mock.calls[0][1] as { citations: unknown[] };
    expect(body.citations).toEqual([
      { kind: "extracted_page", extraction_id: "x-ppt", page_id: "page-22", char_start: start, char_end: end },
    ]);
    expect(JSON.stringify(body)).not.toContain("hauran");
  });

  it("una selecció fora del text de la pàgina falla tancada", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix una cita" }));
    await flush();
    await pickPage(form);
    const title = within(form).getByText("Afegir un requisit a la revisió");
    const range = document.createRange();
    range.selectNodeContents(title);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);

    fireEvent.click(within(form).getByRole("button", { name: "Usa el text seleccionat" }));

    expect(within(form).getByRole("alert").textContent).toBe("Selecciona només text de dins de la pàgina.");
    expect(form.querySelector("blockquote")).toBeNull();
  });

  it("amb un fragment escrit: únic, o res", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix una cita" }));
    await flush();
    await pickPage(form);
    const fragment = within(form).getByLabelText("O escriu un fragment exacte de la pàgina");

    fireEvent.change(fragment, { target: { value: "no hi és" } });
    fireEvent.click(within(form).getByRole("button", { name: "Cerca el fragment" }));
    expect(within(form).getByRole("alert").textContent).toBe("Aquest text no és a la pàgina.");

    fireEvent.change(fragment, { target: { value: "nivell 2 amb el datacenter on es gestionin" } });
    fireEvent.click(within(form).getByRole("button", { name: "Cerca el fragment" }));
    expect(within(form).getByText("«nivell 2 amb el datacenter on es gestionin»")).toBeTruthy();
  });

  it("amb canvis sense desar, Cancel·la i la creu pregunten", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });
    fireEvent.change(within(form).getByLabelText("Títol", { exact: false }), { target: { value: "A mig fer" } });

    fireEvent.click(within(form).getByRole("button", { name: "Tanca" }));
    await flush();
    expect(within(form).getByText("Descartar els canvis no desats?")).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: "Continua editant" }));
    fireEvent.click(within(form).getByRole("button", { name: "Cancel·la" }));
    expect(within(form).getByText("Descartar els canvis no desats?")).toBeTruthy();
    fireEvent.click(within(form).getByRole("button", { name: "Descarta els canvis" }));
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("422 del servidor: missatge segur amb el camp i el formulari es queda", async () => {
    post.mockRejectedValue(
      new ApiError(422, "validation_error", "Les dades no són vàlides.", [{ field: "citations.0.char_end", type: "x", message: "raw" }]),
    );
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Afegir requisit" }));
    await flush();
    const form = screen.getByRole("dialog", { name: "Afegir requisit" });
    fireEvent.change(within(form).getByLabelText("Títol", { exact: false }), { target: { value: "Prova" } });
    fireEvent.change(within(form).getByLabelText("Categoria", { exact: false }), { target: { value: "other" } });
    fireEvent.click(within(form).getByRole("button", { name: "Afegeix el requisit" }));
    await flush();

    expect(within(form).getByRole("alert").textContent).toBe("Les dades no són vàlides. (Evidència)");
    expect((within(form).getByLabelText("Títol", { exact: false }) as HTMLInputElement).value).toBe("Prova");
  });
});

describe("Afegir evidència a un requisit existent", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    role = "technical";
    respond();
  });

  async function openLinking() {
    await renderPage();
    fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: /Volum anual/ }));
    await flush();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Afegir evidència" }));
    await flush();
    const pre = await pickPage(dialog);
    const fragment = within(dialog).getByLabelText("O escriu un fragment exacte de la pàgina");
    fireEvent.change(fragment, { target: { value: "connectivitat a nivell 2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cerca el fragment" }));
    await flush();
    return { dialog, pre };
  }

  const attach = (dialog: HTMLElement) =>
    within(dialog).getByRole("button", { name: /Afegeix l'evidència/ }) as HTMLButtonElement;

  it("afegeix la cita amb la revisió visible; ni l'estat ni l'origen ni el contingut canvien", async () => {
    const updated: CanonicalRequirement = {
      ...SOLVENCY,
      review_revision: 2,
      evidence: [
        ...SOLVENCY.evidence,
        { document: "PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf", page: 22, section: null, quote: "connectivitat a nivell 2", status_label: "Citat manualment" },
      ],
      pages: [9, 22],
    };
    post.mockResolvedValue(updated);
    const { dialog } = await openLinking();

    expect(within(dialog).getByText("«connectivitat a nivell 2»")).toBeTruthy();
    fireEvent.click(attach(dialog));
    await flush();
    await flush();

    const { start, end } = pythonSpan(PAGE_22, "connectivitat a nivell 2");
    expect(post.mock.calls[0]).toEqual([
      `${REVIEW}/items/r-solvency/actions`,
      {
        action: "link_source",
        expected_revision: 1,
        payload: { citation: { kind: "extracted_page", extraction_id: "x-ppt", page_id: "page-22", char_start: start, char_end: end } },
      },
    ]);
    expect(within(dialog).getByText("Evidència afegida.")).toBeTruthy();
    expect(within(dialog).getByText("Citat manualment")).toBeTruthy();
    expect(within(dialog).getByText(/^Versió de revisió \d+$/).textContent).toBe("Versió de revisió 2");
    expect(within(dialog).getAllByText("Pendent de revisar").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Extret automàticament")).toBeTruthy();
    expect(within(dialog).getByText("≥ 450.000 EUR")).toBeTruthy();
    // L'estat no canvia: el resum no es torna a llegir; l'historial, sí.
    expect(calls(`${REVIEW}/overview`)).toBe(1);
    expect(calls(`${REVIEW}/items/r-solvency/history`)).toBe(2);
  });

  it("la mateixa cita ja enllaçada: missatge clar i cap reintent", async () => {
    post.mockRejectedValue(new ApiError(409, "review_source_already_linked", "Aquesta cita ja es evidencia d'aquest element."));
    const { dialog } = await openLinking();

    fireEvent.click(attach(dialog));
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("alert").textContent).toBe("Aquesta evidència ja està vinculada al requisit.");
    expect(calls(`${REVIEW}/requirements/r-solvency`)).toBe(0);
  });

  it("409 de revisió: torna a llegir, conserva la cita i demana tornar-ho a enviar", async () => {
    const newer = { ...SOLVENCY, review_revision: 3 };
    routes[`${REVIEW}/requirements/r-solvency`] = newer;
    post.mockRejectedValueOnce(new ApiError(409, "review_revision_conflict", "Algu altre ha revisat aquest element."));
    const { dialog } = await openLinking();

    fireEvent.click(attach(dialog));
    await flush();
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("alert").textContent).toMatch(/ha canviat des que l'has obert\. La cita triada es conserva/);
    expect(within(dialog).getByText("«connectivitat a nivell 2»")).toBeTruthy();
    expect(within(dialog).getByText(/^Versió de revisió \d+$/).textContent).toBe("Versió de revisió 3");

    post.mockResolvedValueOnce({ ...newer, review_revision: 4 });
    fireEvent.click(attach(dialog));
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
    expect((post.mock.calls[1][1] as { expected_revision: number }).expected_revision).toBe(3);
  });

  it.each([
    [new ApiError(403, "forbidden", "x"), "No tens permisos per modificar aquesta revisió."],
    [new ApiError(422, "source_ref_span_invalid", "El tros citat surt del text d'aquesta pagina."), "El tros citat surt del text d'aquesta pagina."],
    [new TypeError("Failed to fetch"), "No s'ha pogut desar. Torna-ho a provar d'aquí a un moment."],
  ])("error %#: missatge i la cita es conserva", async (error, message) => {
    post.mockRejectedValue(error);
    const { dialog } = await openLinking();

    fireEvent.click(attach(dialog));
    await flush();

    expect(within(dialog).getByRole("alert").textContent).toBe(message);
    expect(within(dialog).getByText("«connectivitat a nivell 2»")).toBeTruthy();
    expect(attach(dialog).disabled).toBe(false);
  });
});
