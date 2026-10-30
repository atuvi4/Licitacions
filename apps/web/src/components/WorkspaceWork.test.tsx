import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { dossierFixture, workspaceFixture } from "@/test/workspace-fixture";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const del = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...args: unknown[]) => get(...args),
      post: (...args: unknown[]) => post(...args),
      patch: (...args: unknown[]) => patch(...args),
      delete: (...args: unknown[]) => del(...args),
    },
  };
});

import { ApiError } from "@/lib/api";
import { lotFixture, offerFixture, totalsFixture } from "@/test/economic-fixture";
import { solutionFixture } from "@/test/technical-fixture";

import { ChecklistSection, NotesSection, PrepareOverview, TasksSection } from "./WorkspaceWork";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
/** Obre una carpeta de la checklist com ho fa el navegador: `open` i l'esdeveniment `toggle`. */
function openFolder(container: HTMLElement, key: string) {
  const folder = container.querySelector(`[data-checklist-folder="${key}"]`) as HTMLDetailsElement;
  folder.open = true;
  fireEvent(folder, new Event("toggle"));
  return folder;
}
const onChange = vi.fn(async () => undefined);
const members = [
  { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  { id: "u2", email: "pere@example.org", full_name: null },
];

describe("Checklist · Tasques · Notes (restil sense canviar la lògica)", () => {
  beforeEach(() => {
    post.mockReset();
    patch.mockReset();
    del.mockReset();
    onChange.mockClear();
  });

  it("checklist: pastilles d'estat reals, reconciliar fa el POST de sempre i explica el resultat", async () => {
    post.mockResolvedValue({ created: 2, carried: 1, historic: 1, total: 6, reason: null });
    patch.mockResolvedValue({});
    const workspace = workspaceFixture({
      members,
      checklist: [
        { id: "c1", workspace_id: "w1", requirement_id: "r1", title: "Aportar comptes anuals", category: "economic_solvency", mandatory: true, status: "blocked", source: "requirement", assigned_to: "u1", assigned_to_name: "Anna Puig", due_at: null, note: null, completed_at: null, created_at: "2026-09-10T09:00:00Z" },
        { id: "c2", workspace_id: "w1", requirement_id: null, title: "Preparar annex 3", category: "other", mandatory: false, status: "completed", source: "manual", assigned_to: null, assigned_to_name: null, due_at: null, note: null, completed_at: "2026-09-11T09:00:00Z", created_at: "2026-09-10T09:00:00Z" },
      ],
    });
    const { container } = render(<ChecklistSection workspace={workspace} onChange={onChange} />);
    // Plegada per defecte: cap fila a la vista fins que s'obre la carpeta.
    expect(screen.queryByText("Aportar comptes anuals")).toBeNull();
    openFolder(container, "A");
    openFolder(container, "execucio");
    const blocked = screen.getByText("Aportar comptes anuals").closest("tr")!;
    expect([...blocked.querySelectorAll("[data-level]")].map((p) => p.getAttribute("data-level"))).toContain("blocking");
    expect(blocked.textContent).toContain("Obligatori");
    expect([...blocked.querySelectorAll("a")].some((a) => a.getAttribute("href") === "/licitacions/t1?tab=requisits")).toBe(true);
    expect(screen.getByText("Preparar annex 3").closest("tr")!.textContent).toContain("Manual");
    expect(container.textContent).toContain("1 de 2 completats");

    fireEvent.change(blocked.querySelectorAll("select")[0], { target: { value: "in_progress" } });
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/checklist/c1", { status: "in_progress" });
    fireEvent.change(blocked.querySelectorAll("select")[1], { target: { value: "" } });
    await flush();
    expect(patch).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/checklist/c1", { assigned_to: null });

    fireEvent.click(screen.getByRole("button", { name: /Reconcilia amb l'anàlisi/ }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/checklist/regenerate", {});
    expect(container.textContent).toContain("2 items nous · 1 item conservat.");
    expect(container.textContent).toContain("1 item queda a l'històric");
    expect(onChange).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Preparar annex 3"), { target: { value: "Signar la declaració" } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix" }));
    await flush();
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/checklist", { title: "Signar la declaració" });
  });

  it("checklist en quatre carpetes plegades amb el resum a la capçalera; el Sobre C porta a l'oferta", () => {
    const at = "2026-10-30T09:00:00Z";
    const base = { workspace_id: "w1", status: "pending" as const, source: "requirement" as const, assigned_to: null, assigned_to_name: null, due_at: null, note: null, completed_at: null, created_at: at };
    const workspace = workspaceFixture({
      members,
      checklist: [
        { ...base, id: "a1", requirement_id: "r1", title: "DEUC signat", category: "administrative_documentation", mandatory: true, status: "completed" },
        { ...base, id: "a2", requirement_id: "r2", title: "Certificat ENS", category: "certification", mandatory: true },
        { ...base, id: "b1", requirement_id: "r3", title: "SIP Trunk de 20+20 canals", category: "technical_requirement", mandatory: true, status: "blocked" },
        { ...base, id: "x1", requirement_id: "r4", title: "Facturació mensual electrònica", category: "contract_condition", mandatory: true },
      ],
    });
    const { container } = render(<ChecklistSection workspace={workspace} onChange={onChange} />);
    const folders = [...container.querySelectorAll("[data-checklist-folder]")] as HTMLDetailsElement[];
    expect(folders.map((folder) => folder.getAttribute("data-checklist-folder"))).toEqual(["A", "B", "C", "execucio"]);
    expect(folders.every((folder) => !folder.open)).toBe(true);
    const summaries = folders.map((folder) => folder.querySelector("summary")!.textContent);
    expect(summaries[0]).toContain("Sobre A · Documentació administrativa per presentar");
    expect(summaries[0]).toContain("1 de 2 a punt · 1 obligatori pendent");
    expect(summaries[1]).toContain("Sobre B · Proposta tècnica");
    expect(summaries[1]).toContain("0 de 1 a punt · 1 obligatori pendent · 1 bloquejat");
    expect(summaries[2]).toContain("Cap ítem");
    expect(summaries[3]).toContain("Condicions d'execució · Obligacions contractuals post-adjudicació; no van a cap sobre");
    expect(container.querySelectorAll("tbody tr").length).toBe(0);

    openFolder(container, "B");
    expect(screen.getByText("SIP Trunk de 20+20 canals")).toBeTruthy();
    expect(screen.queryByText("DEUC signat")).toBeNull();
    openFolder(container, "C");
    const offerLink = within(folders[2]).getByRole("link", { name: "l'oferta econòmica" });
    expect(offerLink.getAttribute("href")).toBe("?pas=preparar&area=oferta");
  });

  it("checklist buida: sortida cap a la reconciliació", () => {
    render(<ChecklistSection workspace={workspaceFixture({ members })} onChange={onChange} />);
    expect(screen.getByText("Encara no hi ha cap item")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Reconcilia amb l'anàlisi/ }).length).toBeGreaterThan(0);
  });

  it("tasques: només les persones de l'empresa, creació amb el payload de sempre, pastilla d'estat", async () => {
    post.mockResolvedValue({});
    patch.mockResolvedValue({});
    const workspace = workspaceFixture({
      members,
      tasks: [
        { id: "t1", workspace_id: "w1", title: "Demana la renovació", description: null, assigned_to: "u2", mandatory: true, assigned_to_name: null, due_at: "2026-09-18T10:00:00Z", status: "in_progress", created_by: null, created_at: "2026-09-10T09:00:00Z", completed_at: null },
      ],
    });
    const { container } = render(<TasksSection workspace={workspace} onChange={onChange} />);
    expect(container.id || container.querySelector("#tasques")).toBeTruthy();
    const row = screen.getByText("Demana la renovació").closest("tr")!;
    expect([...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`)).toContain("En curs[running]");
    const assignee = row.querySelectorAll("select")[1];
    expect([...assignee.options].map((o) => o.textContent)).toEqual(["Sense assignar", "Anna Puig", "pere@example.org"]);
    fireEvent.change(row.querySelectorAll("select")[0], { target: { value: "completed" } });
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/tasks/t1", { status: "completed" });

    fireEvent.change(screen.getByPlaceholderText("Demanar certificat bancari"), { target: { value: "Demanar el certificat de la TGSS" } });
    fireEvent.click(screen.getByRole("button", { name: "Crea" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/tasks", { title: "Demanar el certificat de la TGSS", assigned_to: null, due_at: null });
    expect(onChange).toHaveBeenCalled();
  });

  it("notes: es desen amb el POST de sempre i surten signades i datades", async () => {
    post.mockResolvedValue({});
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Parlat amb el responsable.", created_at: "2026-09-12T09:00:00Z" }],
    });
    const { container } = render(<NotesSection workspace={workspace} onChange={onChange} />);
    expect(container.querySelector("[data-kind='human']")).not.toBeNull();
    expect(container.textContent).toContain("Anna Puig");
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "  Nova nota  " } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix la nota" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes", { text: "Nova nota" });
  });

  it("notes (Bloc 1): es corregeixen al mateix lloc amb el PATCH i diuen quan es van corregir", async () => {
    patch.mockResolvedValue({});
    const workspace = workspaceFixture({
      notes: [
        { id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Primera versió.", created_at: "2026-09-12T09:00:00Z", updated_at: "2026-09-13T10:00:00Z" },
      ],
    });
    const { container } = render(<NotesSection workspace={workspace} onChange={onChange} />);
    expect(container.textContent).toContain("Corregida el");

    fireEvent.click(screen.getByRole("button", { name: /^Corregeix la nota/ }));
    const editor = screen.getByLabelText("Text de la nota") as HTMLTextAreaElement;
    expect(editor.value).toBe("Primera versió.");
    fireEvent.change(editor, { target: { value: "  Versió corregida.  " } });
    fireEvent.click(screen.getByRole("button", { name: "Desa la correcció" }));
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes/n1", { text: "Versió corregida." });
    expect(onChange).toHaveBeenCalled();
  });

  it("notes (Bloc 1): esborrar demana confirmació i fa el DELETE només en confirmar", async () => {
    del.mockResolvedValue(undefined);
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Nota repetida.", created_at: "2026-09-12T09:00:00Z" }],
    });
    const { container } = render(<NotesSection workspace={workspace} onChange={onChange} />);
    expect(container.textContent).not.toContain("Corregida el");

    fireEvent.click(screen.getByRole("button", { name: /^Esborra la nota/ }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "Confirma l'esborrat" })).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel·la" }));
    expect(screen.queryByRole("group", { name: "Confirma l'esborrat" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^Esborra la nota/ }));
    fireEvent.click(screen.getByRole("button", { name: "Esborra-la" }));
    await flush();
    expect(del).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes/n1");
    expect(onChange).toHaveBeenCalled();
  });

  it("notes (Bloc 1): una correcció sense canvis no fa cap crida", async () => {
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Igual.", created_at: "2026-09-12T09:00:00Z" }],
    });
    render(<NotesSection workspace={workspace} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Corregeix la nota/ }));
    fireEvent.click(screen.getByRole("button", { name: "Desa la correcció" }));
    await flush();
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^Corregeix la nota/ })).not.toBeNull();
  });

  it("notes (Bloc 1): sense el rol que treballa els dubtes no es poden corregir ni esborrar", () => {
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Només lectura.", created_at: "2026-09-12T09:00:00Z" }],
    });
    render(<NotesSection workspace={workspace} onChange={onChange} canEdit={false} />);
    expect(screen.getByText("Només lectura.")).not.toBeNull();
    expect(screen.queryByRole("button", { name: /^Corregeix la nota/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Esborra la nota/ })).toBeNull();
  });

  it("notes (Bloc 1): buidar el text no desa res i diu que per treure-la cal esborrar-la", async () => {
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Text.", created_at: "2026-09-12T09:00:00Z" }],
    });
    render(<NotesSection workspace={workspace} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Corregeix la nota/ }));
    fireEvent.change(screen.getByLabelText(/^Text de la nota/), { target: { value: "   " } });
    const save = screen.getByRole("button", { name: "Desa la correcció" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText(/per treure-la, esborra-la/)).not.toBeNull();
    fireEvent.click(save);
    await flush();
    expect(patch).not.toHaveBeenCalled();
  });

  it("notes (Bloc 1): un error es mostra i cancel·lar l'esborra", async () => {
    patch.mockRejectedValue(new Error("No s'ha pogut desar"));
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Abans.", created_at: "2026-09-12T09:00:00Z" }],
    });
    render(<NotesSection workspace={workspace} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /^Corregeix la nota/ }));
    fireEvent.change(screen.getByLabelText(/^Text de la nota/), { target: { value: "Després." } });
    fireEvent.click(screen.getByRole("button", { name: "Desa la correcció" }));
    await flush();
    expect(screen.getByText("No s'ha pogut desar")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Cancel·la" }));
    expect(screen.queryByText("No s'ha pogut desar")).toBeNull();
    expect(screen.getByText("Abans.")).not.toBeNull();
  });
});

describe("Tauler dels tres sobres i «Què et falta per tancar l'oferta?»", () => {
  const requirement = (id: string, category: string, title: string) => ({
    requirement_id: id, title, category, review_status: "unreviewed", tender_document_id: null, extraction_id: null,
    extraction_revision: null, page_number: null, quote: null, document_name: null,
  });
  const assessment = (id: string, category: string, result: string, title: string, params: Record<string, unknown> | null = null) => ({
    id, assessment_run_id: "run1", requirement_id: id, tender_id: "t1", tender_lot_id: null, automatic_result: result,
    reviewed_result: null, effective_result: result, assessment_mode: "automatic", rule_code: "r", explanation_code: "C",
    explanation_params: params, company_value_json: null, requirement_value_json: null, obligation: "mandatory",
    blocker_status: result === "does_not_satisfy" ? "potential" : "none", evidence_strength: "missing",
    review_status: "unreviewed", review_note: null, reviewed_at: null, reviewed_by: null, reviewed_by_name: null,
    calculated_at: "2026-10-30T09:00:00Z", evidence: [], requirement: requirement(id, category, title),
  });
  const compliance = {
    run: { id: "run1", status: "completed", potential_blockers_count: 1, confirmed_blockers_count: 0 },
    assessments: [
      assessment("e1", "economic_solvency", "satisfies", "Volum anual de negocis mínim"),
      assessment("c1", "certification", "needs_review", "Certificat ENS categoria mitjana", { company_certifications: ["ENS categoria Mitjana"] }),
      assessment("p1", "personnel", "needs_review", "Cap de projecte: enginyer de telecomunicacions"),
    ],
    groups: [],
    is_stale: false,
  };
  const solutions = [
    solutionFixture({ id: "s1", title: "1.b · Comunicacions fixes (8 punts)", status: "needs_validation", proposed_solution: "SIP Trunk. [Cal confirmar: les centrals.]" }),
    solutionFixture({ id: "s2", title: "1.c · Dades i Internet (8 punts)", status: "needs_validation", proposed_solution: "FTTH majorista." }),
  ];
  const offer = offerFixture({
    notes: "Marge net previst de l'escandall a les notes.",
    totals: totalsFixture({ total_revenue: "122914.41", line_count: 113 }),
    internal_economics: { total_cost: "55476.83", margin_amount: "67437.58", margin_on_sales_pct: "54.8655", markup_on_cost_pct: "121.5599", incomplete_cost_lines: 0 },
    lots: [lotFixture({ limit: { tender_max: "175000.00", difference_to_maximum: "52085.59", exceeds_lot_max: false } })],
  });
  const finding = (code: string, domain: string) => ({ code, severity: "blocker" as const, domain: domain as "technical", entity_id: null, context: {} });

  function respond(overrides: Record<string, unknown> = {}) {
    const routes: Record<string, unknown> = {
      "/companies/c1/tenders/t1/compliance": compliance,
      "/companies/c1/workspaces/w1/technical-solutions": solutions,
      "/companies/c1/workspaces/w1/economic-offer": offer,
      "/companies/c1/workspaces/w1/dossier": dossierFixture(),
      ...overrides,
    };
    get.mockImplementation(async (path: string) => {
      const value = routes[path];
      if (value instanceof ApiError) throw value;
      if (value === undefined) throw new ApiError(404, "not_found", "No trobat");
      return value;
    });
  }

  beforeEach(() => {
    get.mockReset();
  });

  it("les tres targetes llegeixen el compliment, les solucions, l'oferta i el dossier; la caixa agrupa el que falta", async () => {
    respond();
    const { container } = render(
      <PrepareOverview
        workspace={workspaceFixture()}
        findings={[
          finding("technical_solution_unvalidated", "technical"),
          finding("technical_solution_unvalidated", "technical"),
          finding("mandatory_evidence_missing", "dossier"),
        ]}
      />,
    );
    await flush();
    const card = (key: string) => container.querySelector(`[data-envelope-card="${key}"]`) as HTMLElement;

    const a = card("A").textContent ?? "";
    expect(a).toContain("Solvència econòmica");
    expect(a).toContain("Compleix");
    expect(a).toContain("L'empresa declara: ENS categoria Mitjana");
    expect(a).toContain("20 de 24 obligatoris coberts · 2 sense document · 2 per revisar · 1 caducats");
    expect(within(card("A")).getByRole("link", { name: /Obre el dossier/ }).getAttribute("href")).toBe("?pas=preparar&area=dossier");

    const b = card("B").textContent ?? "";
    expect(b).toContain("2 de 2 capítols redactats");
    expect(b).toContain("16 punts");
    expect(b).toContain("2 pendents de validar");
    expect(b).toContain("Personal per confirmar: «Cap de projecte: enginyer de telecomunicacions»");
    expect(b).toContain("1 punt marcat «Cal confirmar» als capítols");

    const c = card("C").textContent ?? "";
    expect(c).toContain("122.914,41 €");
    expect(c).toContain("29,76 %");
    expect(c).toContain("175.000,00 €");
    expect(c).toContain("67.437,58 €");
    expect(c).toContain("54,9 % sobre vendes");
    expect(c).toContain("Sota el màxim");
    expect(container.querySelector("[data-margin-note]")!.textContent).toContain("El marge net previst de l'escandall és a les notes de l'oferta.");

    const box = container.querySelector("[data-next-actions]") as HTMLElement;
    expect(box.textContent).toContain("Què et falta per tancar l'oferta?");
    const rows = [...box.querySelectorAll("li")].map((li) => li.textContent);
    expect(rows[0]).toContain("Falten documents obligatoris");
    // Avís agregat del dossier: cap número (el servidor no diu quants documents falten).
    expect(rows[0]).not.toMatch(/^\s*\d/);
    expect(rows[1]).toMatch(/^2.*Hi ha una solució tècnica sense validar/);
    expect(rows[2]).toContain("Hi ha punts marcats «Cal confirmar» a la memòria tècnica");
    expect(rows[3]).toContain("El compliment té un possible bloqueig per revisar");
    expect(within(box).getByRole("link", { name: /Obre el compliment/ }).getAttribute("href")).toBe("/licitacions/t1?tab=requisits");
  });

  it("sense oferta ni preparació final: ho diu, no s'inventa cap xifra ni cap «tot a punt»", async () => {
    respond({ "/companies/c1/workspaces/w1/economic-offer": new ApiError(404, "not_found", "No trobat"), "/companies/c1/tenders/t1/compliance": { run: null, assessments: [], is_stale: false } });
    const { container } = render(<PrepareOverview workspace={workspaceFixture()} findings={null} />);
    await flush();
    expect(container.querySelector('[data-envelope-card="C"]')!.textContent).toContain("Encara no hi ha oferta econòmica.");
    expect(container.querySelector('[data-envelope-card="A"]')!.textContent).toContain("Encara no s'ha calculat el compliment");
    expect(container.querySelector("[data-next-actions]")!.textContent).toContain("No s'ha pogut llegir la preparació final");
    expect(container.textContent).not.toContain("Res no impedeix");
  });
});
