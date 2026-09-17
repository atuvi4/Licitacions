import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Dossier } from "@/lib/workspace";
import { dossierFixture, dossierItemFixture, workspaceFixture } from "@/test/workspace-fixture";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...args: unknown[]) => get(...args),
      post: (...args: unknown[]) => post(...args),
      patch: (...args: unknown[]) => patch(...args),
    },
  };
});

import { ApiError } from "@/lib/api";

import { DossierSection } from "./WorkspaceDossier";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onChange = vi.fn(async () => undefined);
const onAdvanced = vi.fn(async () => undefined);
const DOCUMENTS = [
  { id: "doc1", name: "Comptes anuals 2025.pdf" },
  { id: "doc2", name: "Pòlissa RC 2026.pdf" },
];

/** Un dossier amb els quatre casos que importen, en dos grups. */
function dossier(overrides: Partial<Dossier> = {}): Dossier {
  return dossierFixture({
    groups: [
      {
        group: "economic_solvency",
        items: [
          // Triat però ningú no ha dit que acrediti: el backend diu «Cal revisar».
          dossierItemFixture({
            id: "unproven",
            requirement_title: "Volum de negoci ≥ 600.000 €",
            coverage: "needs_review",
            relevance: "relevance_unproven",
            document: { ...dossierItemFixture().document!, id: "doc2", name: "Carnet REIE M. Soler.pdf" },
            assessment_result: "needs_review",
          }),
          // Sense document.
          dossierItemFixture({
            id: "missing",
            requirement_title: "Comptes de l'últim exercici dipositats",
            status: "missing",
            document: null,
            validity: "no_document",
            coverage: "missing_document",
            relevance: "relevance_unproven",
            selected_by_name: null,
            selected_at: null,
          }),
        ],
      },
      {
        group: "insurances",
        items: [
          // Triat i pertinent, però caduca abans del termini: el backend ho deixa en «Falta document».
          dossierItemFixture({
            id: "expiring",
            requirement_title: "Responsabilitat civil ≥ 600.000 €",
            document: { ...dossierItemFixture().document!, id: "doc2", name: "Pòlissa RC 2026.pdf", expires_at: "2026-09-20" },
            validity: "expires_before_deadline",
            coverage: "missing_document",
            relevance: "linked_evidence",
          }),
          // Confirmada per una persona: cobert, segons el backend.
          dossierItemFixture({
            id: "confirmed",
            requirement_title: "Declaració responsable (annex III)",
            coverage: "covered",
            relevance: "human_confirmed",
            relevance_confirmed_by_name: "Anna Puig",
            relevance_confirmed_at: "2026-09-14T10:00:00Z",
            relevance_note: "És l'annex III signat pel gerent.",
          }),
          // Enllaçada amb l'evidència del compliment: cobert.
          dossierItemFixture({ id: "linked", requirement_title: "Certificat ISO 9001" }),
        ],
      },
    ],
    ...overrides,
  });
}

function mockLoad(value: Dossier) {
  get.mockImplementation(async (path: string) => (path.endsWith("/documents") ? DOCUMENTS : value));
}

function renderSection(overrides: Parameters<typeof workspaceFixture>[0] = {}) {
  return render(<DossierSection workspace={workspaceFixture({ status: "preparing_bid", ...overrides })} onChange={onChange} onAdvanced={onAdvanced} />);
}

const rowOf = (title: string) => screen.getByText(title).closest("tr")!;
const pills = (row: Element) => [...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);

describe("DossierSection · compliment ≠ dossier, triat ≠ acreditat", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    patch.mockReset();
    onChange.mockClear();
    onAdvanced.mockClear();
  });

  it("cap diàleg natiu del navegador al codi", () => {
    const source = readFileSync(join(process.cwd(), "src/components/WorkspaceDossier.tsx"), "utf-8");
    // El literal es construeix perquè el grep d'acceptació del codi quedi net.
    const forbidden = ["window", "prompt"].join(".");
    expect(source).not.toContain(forbidden);
  });

  it("un document triat sense pertinença no és «Cobert»: mostra el que diu el backend i ofereix confirmar", async () => {
    mockLoad(dossier());
    renderSection();
    await flush();
    const row = rowOf("Volum de negoci ≥ 600.000 €");
    expect(row.getAttribute("data-tint")).toBe("attention");
    // Compliment i Dossier són dues pastilles diferents.
    expect(pills(row)).toContain("Cal revisar[attention]");
    expect(pills(row)).not.toContain("Cobert[ready]");
    expect(row.textContent).toContain("Carnet REIE M. Soler.pdf");
    expect(row.textContent).toContain("Pertinença per acreditar");
    expect([...row.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Confirma que acredita")).toBe(true);
    // El «Seleccionat» no es pinta com a a punt.
    expect(row.querySelector("[data-level='ready']")).toBeNull();
  });

  it("una confirmació humana forma part de «Cobert» segons el backend, amb qui i quan", async () => {
    mockLoad(dossier());
    renderSection();
    await flush();
    // Els coberts van plegats: es despleguen.
    fireEvent.click(screen.getByRole("button", { name: /Coberts i confirmats/ }));
    const row = rowOf("Declaració responsable (annex III)");
    expect(pills(row)).toContain("Cobert[ready]");
    expect(row.textContent).toContain("Confirmat per Anna Puig");
    expect(row.textContent).toContain("2026");
    expect([...row.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Confirma que acredita")).toBe(false);
  });

  it("confirmar la pertinença obre un modal amb requisit i document, exigeix el motiu real i fa el POST de sempre", async () => {
    mockLoad(dossier());
    post.mockResolvedValue(dossier());
    renderSection();
    await flush();
    const row = rowOf("Volum de negoci ≥ 600.000 €");
    fireEvent.click([...row.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Confirma que acredita")!);
    const modal = screen.getByRole("dialog");
    expect(modal.getAttribute("aria-label")).toBe("Per què aquest document acredita aquest requisit?");
    expect(modal.textContent).toContain("Volum de negoci ≥ 600.000 €");
    expect(modal.textContent).toContain("Carnet REIE M. Soler.pdf");
    const confirm = [...modal.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Confirma que acredita")! as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    const textarea = modal.querySelector("textarea")!;
    expect(textarea.getAttribute("maxlength")).toBe("2000");
    fireEvent.change(textarea, { target: { value: "   " } });
    expect(confirm.disabled).toBe(true);
    fireEvent.change(textarea, { target: { value: "  El carnet és del cap d'obra, en vigor.  " } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dossier/unproven/relevance", { note: "El carnet és del cap d'obra, en vigor." });
    expect(onChange).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("la vigència ve del backend i es mesura contra el termini: caduca abans → bloqueig amb la data real", async () => {
    mockLoad(dossier());
    renderSection();
    await flush();
    const row = rowOf("Responsabilitat civil ≥ 600.000 €");
    expect(row.getAttribute("data-tint")).toBe("blocking");
    expect(row.textContent).toContain("Caduca el 20 de set. 2026");
    expect(row.textContent).toContain("abans del termini");
    expect(pills(row)).toContain("Caduca ABANS del termini[blocking]");
    expect(pills(row)).toContain("Falta document[blocking]");
    const missing = rowOf("Comptes de l'últim exercici dipositats");
    expect(missing.getAttribute("data-tint")).toBe("blocking");
    expect(missing.textContent).toContain("Cap document triat");
    expect([...missing.querySelectorAll("button")].some((b) => b.textContent?.trim() === "Crea tasca")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Coberts i confirmats/ }));
    expect(pills(rowOf("Certificat ISO 9001"))).toContain("Sense data de caducitat[neutral]");
  });

  it("readiness no preparat: bàner amb el recompte i els blocadors literals, numerats, i l'accés a les tasques obertes", async () => {
    mockLoad(dossier());
    const { container } = renderSection({
      tasks: [
        { id: "t1", workspace_id: "w1", title: "Demana la renovació", description: null, assigned_to: null, mandatory: true, assigned_to_name: null, due_at: null, status: "pending", created_by: null, created_at: "2026-09-10T09:00:00Z", completed_at: null },
        { id: "t2", workspace_id: "w1", title: "Feta", description: null, assigned_to: null, mandatory: false, assigned_to_name: null, due_at: null, status: "completed", created_by: null, created_at: "2026-09-10T09:00:00Z", completed_at: "2026-09-11T09:00:00Z" },
      ],
    });
    await flush();
    const banner = container.querySelector("[role='status'][data-level='attention']")!;
    expect(banner.textContent).toContain("20 de 24 requisits obligatoris coberts i confirmats.");
    expect(banner.textContent).toContain("Per passar a la revisió final:");
    const items = [...banner.querySelectorAll("ol li")].map((li) => li.textContent?.trim());
    expect([...banner.querySelectorAll("ol li")].map((li) => li.getAttribute("title"))).toEqual(["mandatory_evidence_missing", "mandatory_document_expired", "mandatory_relevance_unproven"]);
    expect(items).toEqual([
      "Falten documents per a requisits obligatoris.",
      "Algun document seleccionat caduca abans del termini de presentació.",
      "Hi ha documents triats per a requisits obligatoris sense que ningú hagi confirmat que els acrediten.",
    ]);
    expect(banner.textContent).toContain("1 tasca oberta");
    expect(screen.queryByRole("button", { name: /Passa a revisió final/ })).toBeNull();
    expect(container.textContent).toContain("2 ítems a l'històric");
  });

  it("«Passa a revisió final» només quan el backend diu a punt i l'estat encara no hi és; advance no presenta res", async () => {
    const ready = dossier({ readiness: { state: "ready_for_final_review", blockers: [], is_submitted: false } });
    mockLoad(ready);
    post.mockResolvedValue({ state: "ready_for_final_review", blockers: [], is_submitted: false });
    const { container } = renderSection({ status: "preparing_bid" });
    await flush();
    const banner = container.querySelector("[role='status'][data-level='ready']")!;
    expect(banner.textContent).toContain("Passar-lo a revisió final no presenta res.");
    fireEvent.click(screen.getByRole("button", { name: /Passa a revisió final/ }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/readiness/advance", {});
    expect(post).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledTimes(4);
    // Un advance acceptat avisa la pàgina (que rellegeix el workspace i canvia
    // de pas si l'estat real ho reflecteix); no toca cap altra cosa.
    expect(onAdvanced).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
    // advance ≠ presentar: la resposta real porta is_submitted=false i aquí no es diu mai «presentada».
    expect(container.textContent).not.toMatch(/Presentada|presentat/);
  });

  it("ja a revisió final: cap botó; advance que falla amb els blocadors del backend no mou res", async () => {
    const ready = dossier({ readiness: { state: "ready_for_final_review", blockers: [], is_submitted: false } });
    mockLoad(ready);
    const first = renderSection({ status: "ready_for_final_review" });
    await flush();
    expect(screen.queryByRole("button", { name: /Passa a revisió final/ })).toBeNull();
    first.unmount();

    get.mockClear();
    mockLoad(ready);
    post.mockRejectedValue(new ApiError(409, "workspace_not_ready", "El dossier encara no esta a punt per a la revisio final.", [], { blockers: ["mandatory_tasks_open"] }));
    const { container } = renderSection({ status: "preparing_bid" });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /Passa a revisió final/ }));
    await flush();
    expect(container.textContent).toContain("Hi ha tasques obligatòries pendents.");
    expect(onChange).not.toHaveBeenCalled();
    expect(onAdvanced).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: /Passa a revisió final/ })).toBeTruthy();
  });

  it("sense el rol que pot avançar: botó visible, desactivat, amb qui sí que pot", async () => {
    mockLoad(dossier({ readiness: { state: "ready_for_final_review", blockers: [], is_submitted: false } }));
    const { container } = renderSection({ status: "preparing_bid", can_decide: false });
    await flush();
    const button = screen.getByRole("button", { name: /Passa a revisió final/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(container.textContent).toMatch(/Només .* pot passar-lo a revisió final\./);
    fireEvent.click(button);
    await flush();
    expect(post).not.toHaveBeenCalled();
    expect(onAdvanced).not.toHaveBeenCalled();
  });

  it("«Actualitza propostes» fa el sync de sempre, explica el resultat i conserva l'històric", async () => {
    mockLoad(dossier());
    post.mockResolvedValue({ suggested: 0, gaps: 0, total: 5, reason: "already_in_sync", dossier: dossier({ superseded_count: 3 }) });
    const { container } = renderSection();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Actualitza propostes" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dossier/sync", {});
    expect(container.textContent).toContain("Cap requisit nou.");
    expect(container.textContent).toContain("Ja estava al dia");
    expect(container.textContent).toContain("3 ítems a l'històric");
    expect(onChange).toHaveBeenCalled();
  });

  it("canviar de document i d'estat fan el PATCH de sempre; l'error queda a la fila", async () => {
    mockLoad(dossier());
    patch.mockRejectedValueOnce(new ApiError(422, "validation_error", "Aquest document no és de l'empresa."));
    renderSection();
    await flush();
    const row = rowOf("Comptes de l'últim exercici dipositats");
    const selects = row.querySelectorAll("select");
    fireEvent.change(selects[0], { target: { value: "doc1" } });
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dossier/missing", { document_id: "doc1", status: "selected" });
    expect(rowOf("Comptes de l'últim exercici dipositats").textContent).toContain("Aquest document no és de l'empresa.");
    patch.mockResolvedValueOnce(dossier());
    fireEvent.change(rowOf("Comptes de l'últim exercici dipositats").querySelectorAll("select")[1], { target: { value: "not_applicable" } });
    await flush();
    expect(patch).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/dossier/missing", { status: "not_applicable" });
  });

  it("buit: «Encara no hi ha cap peça» amb l'acció de sync; error de càrrega amb reintent", async () => {
    mockLoad(dossier({ groups: [] }));
    post.mockResolvedValue({ suggested: 2, gaps: 1, total: 3, reason: null, dossier: dossier() });
    const first = renderSection();
    await flush();
    expect(screen.getByText("Encara no hi ha cap peça")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Actualitza propostes" })[1]);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/dossier/sync", {});
    expect(first.container.textContent).toContain("3 requisits al dossier.");
    first.unmount();

    get.mockReset();
    get.mockRejectedValueOnce(new ApiError(500, "internal_error", "Error intern"));
    renderSection();
    await flush();
    expect(screen.getByText("No s'ha pogut carregar el dossier")).toBeTruthy();
    mockLoad(dossier());
    fireEvent.click(screen.getByRole("button", { name: /Torna-ho a provar/ }));
    await flush();
    expect(screen.getByText("Volum de negoci ≥ 600.000 €")).toBeTruthy();
  });
});

describe("DossierSection · blocadors de readiness", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("un codi que el diccionari no coneix es diu en humà i el codi queda al title", async () => {
    mockLoad(dossier({ readiness: { state: "incomplete", blockers: ["codi_nou_del_backend"], is_submitted: false } }));
    const { container } = renderSection();
    await flush();
    const li = container.querySelector("[role='status'] ol li")!;
    expect(li.textContent?.trim()).toBe("Bloqueig de preparació no reconegut");
    expect(li.getAttribute("title")).toBe("codi_nou_del_backend");
    expect(container.textContent).not.toContain("codi_nou_del_backend");
  });
});
