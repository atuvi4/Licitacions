import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PackageDetail, PackageSummary } from "@/lib/workspace";
import { readinessFixture } from "@/test/readiness-fixture";
import { manifestV1, manifestV2 } from "@/test/manifest-fixture";
import { offerFixture } from "@/test/economic-fixture";
import {
  packageApprovalFixture,
  packageDetailFixture,
  packageItemFixture,
  packageSummaryFixture,
  workspaceFixture,
} from "@/test/workspace-fixture";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) } };
});

import { ApiError } from "@/lib/api";

import { ReadinessProvider } from "./ReadinessProvider";
import { SubmissionPackageSection } from "./SubmissionPackage";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onChange = vi.fn(async () => undefined);

/** Simula GET …/packages i GET …/packages/{id} amb el que hi hagi a cada moment. */
/** La preparació final que veu el pas Tancar. Per defecte, a punt. */
let readiness = readinessFixture();

function mockLoad(list: PackageSummary[], details: Record<string, PackageDetail>) {
  get.mockImplementation(async (path: string) => {
    if (path.includes("/final-readiness")) return readiness;
    if (path.endsWith("/economic-offer")) return offerFixture();
    if (path.endsWith("/manifest")) return manifestV2();
    if (path.endsWith("/packages")) return list;
    const id = path.split("/").pop()!;
    return details[id];
  });
}

function renderSection(overrides: Parameters<typeof workspaceFixture>[0] = {}) {
  return render(
    <ReadinessProvider companyId="c1" workspaceId="w1">
      <SubmissionPackageSection
        workspace={workspaceFixture({ status: "ready_for_final_review", readiness: "ready_for_final_review", readiness_blockers: [], ...overrides })}
        onChange={onChange}
        signerName="Anna Puig"
      />
    </ReadinessProvider>,
  );
}

const approveButton = () => screen.getByRole("button", { name: "Aprova per presentar" }) as HTMLButtonElement;
const dialog = () => screen.getByRole("dialog");
const dialogSubmit = () => [...dialog().querySelectorAll("button")].find((b) => b.textContent?.trim() === "Aprova per presentar")! as HTMLButtonElement;

describe("SubmissionPackageSection · congelar, revisar, aprovar; aprovar no és presentar", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("capçalera i manifest amb només camps reals: revisió, estat, hash, documents, bytes, vigència", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    const { container } = renderSection();
    await flush();
    expect(screen.getByText("Revisió 1 del paquet")).toBeTruthy();
    expect(container.textContent).toContain("Congelat");
    expect(container.textContent).toContain("9f21c3b7");
    expect([...container.querySelectorAll("a")].some((a) => a.getAttribute("href") === "/bff/companies/c1/workspaces/w1/packages/p1/manifest")).toBe(true);
    const headers = [...container.querySelectorAll("thead th")].map((th) => th.textContent?.trim());
    expect(headers).toEqual(["#", "Document", "Acredita", "Bytes", "El dia del termini"]);
    const row = screen.getByText("Pòlissa RC 2026.pdf").closest("tr")!;
    expect(row.textContent).toContain("Responsabilitat civil ≥ 600.000 €");
    expect(row.querySelector("[title='7b21e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b']")).not.toBeNull();
    expect(row.textContent).toContain("7b21e4f5");
    expect([...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`)).toContain("Vigent el dia del termini[ready]");
    expect(row.textContent).toContain("12 d’oct. 2026");
    expect(container.textContent).toContain("2 documents · 2 de 2 obligatoris coberts");
    expect(container.textContent).toContain("El manifest congela la identitat, les metadades i el hash dels documents");
    expect(container.textContent).not.toMatch(/congelat per|Congelat per/);
  });

  it("changed_since_freeze: fila en bloqueig amb «Canviat des de congelar» i el motiu dels bytes", async () => {
    const detail = packageDetailFixture({ items: [packageItemFixture(), packageItemFixture({ id: "pi2", filename: "Pòlissa RC 2026.pdf", position: 1, changed_since_freeze: true })] });
    mockLoad([packageSummaryFixture()], { p1: detail });
    renderSection();
    await flush();
    const row = screen.getByText("Pòlissa RC 2026.pdf").closest("tr")!;
    expect(row.getAttribute("data-tint")).toBe("blocking");
    expect(row.textContent).toContain("Els bytes d'ara no són els congelats");
    expect([...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`)).toContain("Canviat des de congelar[blocking]");
  });

  it("stale: bàner de bloqueig amb els motius, aprovació desactivada amb la raó i «Congela la revisió N+1» que congela de debò", async () => {
    const stale = packageDetailFixture({
      revision_number: 2,
      id: "p2",
      is_stale: true,
      can_approve: false,
      stale_reasons: ["manifest_changed", "document_expired_for_deadline"],
      items: [packageItemFixture({ changed_since_freeze: true }), packageItemFixture({ id: "pi2", filename: "B.pdf", position: 1 })],
    });
    mockLoad([packageSummaryFixture({ id: "p2", revision_number: 2 }), packageSummaryFixture()], { p2: stale, p1: packageDetailFixture() });
    const fresh = packageDetailFixture({ id: "p3", revision_number: 3 });
    post.mockResolvedValue({ created: true, package: fresh });
    const { container } = renderSection();
    await flush();
    const banner = container.querySelector("[role='status'][data-level='blocking']")!;
    expect(banner.textContent).toContain("1 document ha canviat des de congelar");
    expect(banner.textContent).toContain("La candidatura actual ja no coincideix amb aquesta revisió congelada.");
    expect(banner.textContent).toContain("Algun document del paquet ja no serà vigent el dia del termini.");
    expect(approveButton().disabled).toBe(true);
    expect(container.textContent).toContain("Desactivat perquè aquesta revisió ja no descriu els documents d'ara.");
    fireEvent.click(approveButton());
    expect(screen.queryByRole("dialog")).toBeNull();

    mockLoad([packageSummaryFixture({ id: "p3", revision_number: 3 }), packageSummaryFixture({ id: "p2", revision_number: 2, status: "superseded" }), packageSummaryFixture()], { p3: fresh });
    fireEvent.click(screen.getByRole("button", { name: "Congela la revisió 3" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/packages", {});
    expect(screen.getByText("Revisió 3 del paquet")).toBeTruthy();
    expect(onChange).toHaveBeenCalled();
    // Les revisions anteriors continuen a l'historial.
    const history = screen.getByText("Revisions").closest("section")!;
    expect(history.textContent).toContain("Revisió 2");
    expect(history.textContent).toContain("Revisió 1");
    expect(history.textContent).toContain("Superat");
  });

  it("una revisió nova no hereta l'aprovació de l'anterior; l'aprovació antiga continua visible a l'historial", async () => {
    const approvedOld = packageSummaryFixture({ status: "superseded", is_approved: true, approved_at: "2026-09-16T10:20:00Z", approved_by_name: "Anna Puig" });
    const current = packageDetailFixture({ id: "p2", revision_number: 2, approval: null, approvals: [] });
    mockLoad([packageSummaryFixture({ id: "p2", revision_number: 2 }), approvedOld], { p2: current });
    const { container } = renderSection();
    await flush();
    const card = screen.getByText("Aprovem aquest paquet?").closest("section")!;
    expect(card.textContent).not.toContain("Aprovat per presentar");
    expect(card.querySelector("[data-kind='human']")).toBeNull();
    expect(approveButton().disabled).toBe(false);
    const history = screen.getByText("Revisions").closest("section")!;
    expect(history.textContent).toContain("aprovada per Anna Puig");
    expect(container.querySelectorAll("a, button")).not.toSatisfy((nodes: NodeListOf<Element>) => [...nodes].some((n) => /Compara/i.test(n.textContent ?? "")));
  });

  it("simulation_only mai és aprovable: botó visible, desactivat, amb la raó; cap modal", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture({ simulation_only: true, can_approve: false, analysis_bases: ["simulation"] }) });
    const { container } = renderSection();
    await flush();
    expect(approveButton().disabled).toBe(true);
    expect(container.textContent).toContain("Paquet de simulació: no es pot aprovar.");
    fireEvent.click(approveButton());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("!can_sign: botó visible amb PermissionNote dels rols reals; !can_approve per readiness: la raó humanitzada", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture({ can_sign: false }) });
    const first = renderSection();
    await flush();
    expect(approveButton().disabled).toBe(true);
    expect(first.container.textContent).toMatch(/Només .* pot signar l'aprovació\./);
    first.unmount();

    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture({ can_approve: false, readiness: "incomplete", readiness_blockers: ["mandatory_tasks_open"] }) });
    const second = renderSection();
    await flush();
    expect(approveButton().disabled).toBe(true);
    expect(second.container.textContent).toContain("Hi ha tasques obligatòries pendents.");
    expect(second.container.textContent).not.toContain("mandatory_tasks_open");
  });

  it("el modal diu exactament què s'aprova i el POST és el real; aprovar no canvia is_submitted", async () => {
    const detail = packageDetailFixture();
    const approved = packageDetailFixture({ status: "approved", approval: packageApprovalFixture(), approvals: [packageApprovalFixture()], can_approve: false, workspace_status: "approved_for_submission", is_submitted: false });
    mockLoad([packageSummaryFixture()], { p1: detail });
    post.mockResolvedValue(approved);
    const { container } = renderSection();
    await flush();
    expect(container.textContent).toContain("Estat: A revisió final");
    expect(container.textContent).toContain("encara no s'ha presentat res");
    fireEvent.click(approveButton());
    const modal = dialog();
    expect(modal.textContent).toContain("Estàs aprovant exactament la revisió 1 d'aquest paquet");
    expect(modal.textContent).toContain("2 documents");
    expect(modal.textContent).toContain("9f21c3b7");
    expect(modal.textContent).toContain("21 de set. 2026");
    expect(modal.textContent).toContain("Anna Puig");
    expect(modal.textContent).toContain("Aprovar aquest paquet NO el presenta a la plataforma pública.");
    fireEvent.change(modal.querySelector("textarea")!, { target: { value: "  Dossier revisat.  " } });
    mockLoad([packageSummaryFixture({ status: "approved", is_approved: true, approved_by_name: "Anna Puig", approved_at: "2026-09-16T10:20:00Z" })], { p1: approved });
    fireEvent.click(dialogSubmit());
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/packages/p1/approvals", { decision: "approved_for_submission", comment: "Dossier revisat." });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onChange).toHaveBeenCalled();
    // Aprovat, amb qui, quan, hash i comentari; i continua sense presentar.
    const card = screen.getByText("Aprovem aquest paquet?").closest("section")!;
    expect(card.textContent).toContain("Aprovat per presentar");
    expect(card.querySelector("[data-kind='human']")).not.toBeNull();
    expect(card.textContent).toContain("Anna Puig");
    expect(card.textContent).toContain("2026");
    expect(card.textContent).toContain("9f21c3b7");
    expect(card.textContent).toContain("Dossier revisat.");
    expect(container.textContent).toContain("Estat: Aprovada per presentar");
    expect(container.textContent).toContain("encara no s'ha presentat res");
  });

  it("comentari buit → null; error d'aprovació del backend dins del modal, sense tancar-lo", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    post.mockRejectedValueOnce(new ApiError(409, "package_stale", "El paquet ja no descriu el que hi ha ara: cal congelar una revisio nova.", [], { reasons: ["manifest_changed"] }));
    renderSection();
    await flush();
    fireEvent.click(approveButton());
    fireEvent.click(dialogSubmit());
    await flush();
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/packages/p1/approvals", { decision: "approved_for_submission", comment: null });
    expect(dialog().textContent).toContain("cal congelar una revisio nova");
    expect(dialog().textContent).toContain("La candidatura actual ja no coincideix amb aquesta revisió congelada.");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("aprovar no és presentar: tres passos, el tercer sense botó, i cap acció de presentar ni de comparar (C-7, C-8)", async () => {
    mockLoad([packageSummaryFixture({ id: "p2", revision_number: 2 }), packageSummaryFixture({ status: "superseded" })], { p2: packageDetailFixture({ id: "p2", revision_number: 2 }) });
    const { container } = renderSection();
    await flush();
    const panel = screen.getByText("Aprovar no és presentar").closest("section")!;
    const steps = [...panel.querySelectorAll("ol li")].map((li) => li.textContent ?? "");
    expect(steps).toHaveLength(3);
    expect(steps[0]).toMatch(/Descarrega/);
    expect(steps[1]).toMatch(/plataforma/);
    expect(steps[2]).toMatch(/constància/);
    expect(panel.querySelectorAll("ol li:nth-child(3) button, ol li:nth-child(3) a")).toHaveLength(0);
    const actions = [...container.querySelectorAll("button, a")].map((n) => n.textContent?.trim() ?? "");
    expect(actions.filter((t) => /presenta/i.test(t))).toEqual(["Aprova per presentar"]);
    expect(actions.some((t) => /compara/i.test(t))).toBe(false);
    expect(actions.some((t) => /marca/i.test(t))).toBe(false);
  });

  it("buit i preparació incompleta: acció desactivada i bàner amb el recompte d'S6 (no és un problema de permisos)", async () => {
    // El veredicte el dona S6, no el camp de dossier del workspace.
    readiness = readinessFixture({
      findings: [{ code: "mandatory_evidence_missing", severity: "blocker", domain: "dossier", entity_id: null, context: {} }],
    });
    mockLoad([], {});
    const { container } = renderSection({ status: "preparing_bid", readiness: "incomplete", readiness_blockers: ["mandatory_evidence_missing"] });
    await flush();
    expect(screen.getByText("Encara no s'ha congelat cap paquet")).toBeTruthy();
    const freeze = screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement;
    expect(freeze.disabled).toBe(true);
    const banner = container.querySelector("[role='status'][data-level='attention']")!;
    expect(banner.textContent).toContain("Queden 1 bloqueig per resoldre");
    expect(banner.textContent).toContain("Torna a Preparar");
    expect(container.textContent).not.toMatch(/Només .* pot/);
    expect(container.textContent).toContain("Estat: Preparant oferta");
    expect(container.textContent).toContain("encara no s'ha presentat res");
  });

  it("buit i a punt: congelar crea la revisió 1; un freeze fallit no substitueix res i s'explica", async () => {
    mockLoad([], {});
    post.mockRejectedValueOnce(new ApiError(409, "package_not_ready", "El dossier encara no esta a punt per congelar-lo.", [], { blockers: ["mandatory_tasks_open"] }));
    const { container } = renderSection({ status: "preparing_bid" });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Congela la proposta" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/packages", {});
    expect(screen.getByText("No s'ha pogut congelar la revisió")).toBeTruthy();
    expect(container.textContent).toContain("Hi ha tasques obligatòries pendents.");
    expect(screen.getByText("Encara no s'ha congelat cap paquet")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();

    post.mockResolvedValueOnce({ created: true, package: packageDetailFixture() });
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    fireEvent.click(screen.getByRole("button", { name: "Congela la proposta" }));
    await flush();
    expect(screen.getByText("Revisió 1 del paquet")).toBeTruthy();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("sense el rol per congelar: botó visible i desactivat amb PermissionNote; error de càrrega amb reintent", async () => {
    mockLoad([], {});
    const first = renderSection({ status: "preparing_bid", can_decide: false });
    await flush();
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(true);
    expect(first.container.textContent).toMatch(/Només .* pot congelar el paquet\./);
    first.unmount();

    get.mockReset();
    get.mockRejectedValueOnce(new ApiError(500, "internal_error", "Error intern"));
    renderSection();
    await flush();
    expect(screen.getByText("No s'ha pogut carregar el paquet")).toBeTruthy();
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    fireEvent.click(screen.getByRole("button", { name: /Torna-ho a provar/ }));
    await flush();
    expect(screen.getByText("Revisió 1 del paquet")).toBeTruthy();
  });
});

describe("Tancar · la porta d'S6", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  const blocking = (...codes: string[]) =>
    readinessFixture({
      findings: codes.map((code) => ({ code, severity: "blocker" as const, domain: "economic" as const, entity_id: null, context: {} })),
    });

  it("amb bloquejos no es pot congelar, i es diuen quants", async () => {
    readiness = blocking("economic_offer_not_started", "economic_lot_empty");
    mockLoad([], {});
    renderSection();
    await flush();
    expect(screen.getByText(/Queden 2 bloquejos per resoldre/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
  });

  it("el bàner porta a Preparar", async () => {
    readiness = blocking("economic_lot_empty");
    mockLoad([], {});
    renderSection();
    await flush();
    const link = screen.getByRole("link", { name: /Torna a Preparar/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/candidatures/w1?pas=preparar");
  });

  it("si la preparació no es pot llegir, tampoc no es congela: desconegut no és a punt", async () => {
    get.mockImplementation(async (path: string) => {
      if (path.includes("/final-readiness")) throw new ApiError(500, "internal_error", "Error intern");
      if (path.endsWith("/packages")) return [];
      return undefined;
    });
    renderSection();
    await flush();
    expect(screen.getByText(/No s'ha pogut llegir la preparació final/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a punt: resum real del que es congelaria i l'acció habilitada", async () => {
    mockLoad([], {});
    const { container } = renderSection();
    await flush();
    expect(screen.getByText("Preparació completa")).toBeTruthy();
    // Dades reals: cobertura del workspace, abast d'S6 i total de l'oferta.
    expect(container.textContent).toContain("Abast revisat");
    expect(container.textContent!.replace(/\u00a0/g, " ")).toContain("11.100,00");
    // I cap cobertura requisit → solució inventada.
    expect(container.textContent).not.toMatch(/\d+ requisits · \d+ resolts/);
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("congelar crida l'endpoint real una sola vegada, encara que es premi dues", async () => {
    mockLoad([], {});
    let resolve: ((value: unknown) => void) | null = null;
    post.mockImplementation(() => new Promise((r) => { resolve = r; }));
    renderSection();
    await flush();
    const button = screen.getByRole("button", { name: "Congela la proposta" });
    await act(async () => {
      fireEvent.click(button);
      fireEvent.click(button);
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/companies/c1/workspaces/w1/packages");
    await act(async () => { resolve?.({}); });
  });

  it("si la preparació ha canviat entre el render i el clic, es rebutja sense trencar res", async () => {
    mockLoad([], {});
    post.mockRejectedValue(new ApiError(409, "package_not_ready", "La candidatura ja no esta a punt."));
    const { container } = renderSection();
    await flush();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Congela la proposta" })); });
    await flush();
    expect(container.textContent).toContain("La candidatura ja no esta a punt.");
    expect(screen.getByRole("link", { name: /Torna a Preparar/ })).toBeTruthy();
    // I es torna a preguntar la preparació: el que sabíem pot haver caducat.
    expect(get.mock.calls.filter(([path]) => String(path).includes("/final-readiness")).length).toBe(2);
  });
});

describe("Tancar · manifest v2", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  async function openManifest() {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    renderSection();
    await flush();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Veure el manifest" })); });
    await flush();
    return screen.getAllByRole("dialog").at(-1)!;
  }

  it("es reconeix el format actual i es llegeix en paraules", async () => {
    const dialog = await openManifest();
    expect(dialog.textContent).toContain("Format actual");
    expect(dialog.textContent).toContain("comptes-2025.pdf");
    expect(dialog.textContent).toContain("Commutació d'accés amb redundància");
    expect(dialog.textContent).toContain("Commutadors d'accés 48p");
    expect(dialog.textContent!.replace(/\u00a0/g, " ")).toContain("127.240,00");
    expect(dialog.textContent).toContain("Base d'aprovació");
  });

  it("no ensenya mai cap xifra interna", async () => {
    const dialog = await openManifest();
    const html = dialog.innerHTML;
    for (const secret of ["Cost intern", "Marge", "Markup", "unit_cost", "margin_on_sales"]) {
      expect(html).not.toContain(secret);
    }
  });

  it("les empremtes no manen a la vista principal: van al detall tècnic", async () => {
    const dialog = await openManifest();
    const details = dialog.querySelector("details")!;
    expect(details.textContent).toContain("submission-manifest-v2");
    expect(details.textContent).toContain("Empremta de l'abast tècnic");
    // El resum d'aprovació parla de comptes, no d'empremtes.
    const basis = [...dialog.querySelectorAll("section")].find((node) => node.textContent?.includes("Base d'aprovació"))!;
    expect(basis.textContent).toContain("2 evidències");
    expect(basis.textContent).not.toContain("a1b2c3d4");
  });

  it("un manifest del format anterior ho diu i no fingeix contingut que no tenia", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    get.mockImplementation(async (path: string) => {
      if (path.includes("/final-readiness")) return readiness;
      if (path.endsWith("/economic-offer")) return offerFixture();
      if (path.endsWith("/manifest")) return manifestV1();
      if (path.endsWith("/packages")) return [packageSummaryFixture()];
      return packageDetailFixture();
    });
    renderSection();
    await flush();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Veure el manifest" })); });
    await flush();
    const dialog = screen.getAllByRole("dialog").at(-1)!;
    expect(dialog.textContent).toContain("Format anterior");
    expect(dialog.textContent).toContain("no descrivia ni la solució tècnica ni l'oferta econòmica");
    expect(dialog.textContent).toContain("comptes-2025.pdf");
    expect(dialog.textContent).not.toContain("Commutació d'accés");
  });

  it("un manifest que no es pot llegir no peta ni es dona per bo", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    get.mockImplementation(async (path: string) => {
      if (path.includes("/final-readiness")) return readiness;
      if (path.endsWith("/economic-offer")) return offerFixture();
      if (path.endsWith("/manifest")) throw new ApiError(500, "internal_error", "Error intern");
      if (path.endsWith("/packages")) return [packageSummaryFixture()];
      return packageDetailFixture();
    });
    renderSection();
    await flush();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Veure el manifest" })); });
    await flush();
    const dialog = screen.getAllByRole("dialog").at(-1)!;
    expect(dialog.textContent).toContain("No s'ha pogut carregar el manifest");
    expect(dialog.textContent).not.toContain("Format actual");
  });
});

describe("Tancar · materialitat i compatibilitat v1", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("la caducitat la diu el servidor: la pantalla no la dedueix de la revisió de l'oferta", async () => {
    // L'oferta ha canviat de revisió (un canvi només de cost intern la mou),
    // però el servidor no marca el paquet com a caducat.
    get.mockImplementation(async (path: string) => {
      if (path.includes("/final-readiness")) return readiness;
      if (path.endsWith("/economic-offer")) return offerFixture({ revision: 99 });
      if (path.endsWith("/manifest")) return manifestV2();
      if (path.endsWith("/packages")) return [packageSummaryFixture()];
      return packageDetailFixture({ is_stale: false, stale_reasons: [] });
    });
    const { container } = renderSection();
    await flush();
    expect(container.textContent).not.toContain("Paquet obsolet");
    expect(container.textContent).not.toContain("Cal congelar");
    expect(screen.getByRole("button", { name: "Aprova per presentar" })).toBeTruthy();
  });

  it("quan el servidor diu que ha caducat, es diu sense cap diferència camp a camp", async () => {
    const stale = packageDetailFixture({ is_stale: true, stale_reasons: ["manifest_changed"], can_approve: false });
    mockLoad([packageSummaryFixture()], { p1: stale });
    const { container } = renderSection();
    await flush();
    expect(container.textContent).toContain("La candidatura actual ja no coincideix amb aquesta revisió congelada.");
    expect(container.textContent).not.toMatch(/substituït per|preu .* → |abans .* ara/);
  });

  it("una revisió del format anterior, congelada i sense aprovar, queda superada i ho explica", async () => {
    const legacy = packageDetailFixture({
      status: "superseded",
      is_stale: true,
      stale_reasons: ["manifest_version_outdated"],
      can_approve: false,
      approval: null,
    });
    mockLoad([packageSummaryFixture({ status: "superseded" })], { p1: legacy });
    const { container } = renderSection();
    await flush();
    expect(container.textContent).toContain("format anterior");
    expect(screen.getByText("Format anterior")).toBeTruthy();
    // I es pot fer la substitució: congelar-ne una de nova.
    expect(screen.getByRole("button", { name: /Congela la revisió 2/ })).toBeTruthy();
  });

  it("una revisió superada per una de posterior NO és del format anterior", async () => {
    // Superada i del format antic són dues coses diferents: només el motiu
    // real del servidor distingeix la segona.
    const superseded = packageDetailFixture({
      status: "superseded",
      is_stale: true,
      stale_reasons: ["superseded"],
      can_approve: false,
    });
    mockLoad([packageSummaryFixture({ status: "superseded" })], { p1: superseded });
    const { container } = renderSection();
    await flush();
    expect(container.textContent).toContain("superada");
    expect(screen.queryByText("Format anterior")).toBeNull();
    expect(container.textContent).not.toContain("format anterior");
  });

  it("una revisió del format anterior JA APROVADA continua aprovada i no es força a recongelar", async () => {
    const approved = packageDetailFixture({
      status: "approved",
      approval: packageApprovalFixture({ approved_by_name: "Anna Puig" }),
      can_approve: false,
      is_stale: false,
      stale_reasons: [],
    });
    mockLoad([packageSummaryFixture({ status: "approved", is_approved: true, approved_by_name: "Anna Puig" })], { p1: approved });
    const { container } = renderSection();
    await flush();
    expect(screen.getAllByText("Aprovat per presentar").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("Anna Puig");
    expect(container.textContent).not.toContain("Format anterior");
    expect(container.textContent).not.toContain("cal congelar una revisió nova");
  });
});

describe("Tancar · aprovar no és presentar", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("una revisió aprovada diu «Aprovada», mai «Presentada»", async () => {
    const approved = packageDetailFixture({
      status: "approved",
      approval: packageApprovalFixture(),
      can_approve: false,
      is_submitted: false,
    });
    mockLoad([packageSummaryFixture({ status: "approved", is_approved: true })], { p1: approved });
    const { container } = renderSection();
    await flush();
    const text = container.textContent!;
    expect(text).not.toMatch(/\bPresentada\b|\bEnviada\b|Licitació presentada/);
    expect(text).toContain("encara no s'ha presentat res");
    const actions = [...container.querySelectorAll("button, a")].map((node) => node.textContent?.trim() ?? "");
    expect(actions.some((label) => /^Presenta/i.test(label))).toBe(false);
  });

  it("es pot rebutjar una revisió, i el POST porta la decisió real", async () => {
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    post.mockResolvedValue({});
    renderSection();
    await flush();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Rebutja aquesta revisió/ })); });
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("es conserva a l'historial");
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "Aprova per presentar" })); });
    await flush();
    const [path, body] = post.mock.calls.at(-1) as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/packages/p1/approvals");
    expect(body).toEqual({ decision: "rejected", comment: null });
  });

  it("l'historial es conserva i no s'hi pot editar res", async () => {
    const current = packageDetailFixture({ id: "p2", revision_number: 2 });
    mockLoad(
      [
        packageSummaryFixture({ id: "p2", revision_number: 2 }),
        packageSummaryFixture({ id: "p1", revision_number: 1, status: "approved", is_approved: true, approved_by_name: "Anna Puig", approved_at: "2026-09-16T10:20:00Z" }),
      ],
      { p2: current },
    );
    const { container } = renderSection();
    await flush();
    // La llista de revisions: l'única que enumera totes dues.
    const history = [...container.querySelectorAll("ol")].find(
      (node) => node.textContent?.includes("Revisió 1") && node.textContent.includes("Revisió 2"),
    )!;
    expect(history.textContent).toContain("Revisió 1");
    expect(history.textContent).toContain("Revisió 2");
    expect(history.textContent).toContain("Anna Puig");
    // Cap acció que reescrigui la història.
    const actions = [...history.querySelectorAll("button")].map((node) => node.textContent?.trim() ?? "");
    expect(actions.some((label) => /Elimina|Esborra|Edita|Desfés/i.test(label))).toBe(false);
    expect(container.textContent).toContain("Cap revisió no s'esborra");
  });
});

describe("Tancar · el manifest de cada revisió", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  /** Historial de dues revisions; la 1 és del format anterior i la 2 de l'actual. */
  function mockHistory() {
    get.mockImplementation(async (path: string) => {
      if (path.includes("/final-readiness")) return readiness;
      if (path.endsWith("/economic-offer")) return offerFixture();
      if (path.endsWith("/packages/p1/manifest")) return manifestV1();
      if (path.endsWith("/packages/p2/manifest")) return manifestV2();
      if (path.endsWith("/packages")) {
        return [
          packageSummaryFixture({ id: "p2", revision_number: 2 }),
          packageSummaryFixture({ id: "p1", revision_number: 1, status: "approved", is_approved: true, approved_by_name: "Anna Puig" }),
        ];
      }
      return packageDetailFixture({ id: "p2", revision_number: 2 });
    });
  }

  const manifestCalls = () => get.mock.calls.map(([path]) => String(path)).filter((path) => path.endsWith("/manifest"));

  it("totes les revisions de l'historial en tenen una, no només l'actual", async () => {
    mockHistory();
    renderSection();
    await flush();
    const history = [...document.querySelectorAll("ol")].find(
      (node) => node.textContent?.includes("Revisió 1") && node.textContent.includes("Revisió 2"),
    )!;
    const buttons = [...history.querySelectorAll("li")].map((item) =>
      within(item as HTMLElement).queryByRole("button", { name: "Veure el manifest" }),
    );
    expect(buttons).toHaveLength(2);
    expect(buttons.every(Boolean)).toBe(true);
  });

  it("no es demana cap manifest fins que algú en vol veure un", async () => {
    mockHistory();
    renderSection();
    await flush();
    expect(manifestCalls()).toEqual([]);
  });

  it("cada botó demana el manifest de la seva revisió", async () => {
    mockHistory();
    renderSection();
    await flush();
    const rows = [...document.querySelectorAll("ol li")] as HTMLElement[];
    const first = rows.find((row) => row.textContent?.includes("Revisió 2"))!;
    await act(async () => { fireEvent.click(within(first).getByRole("button", { name: "Veure el manifest" })); });
    await flush();
    expect(manifestCalls()).toEqual(["/companies/c1/workspaces/w1/packages/p2/manifest"]);
    expect(screen.getAllByRole("dialog").at(-1)!.textContent).toContain("Manifest de la revisió 2");
  });

  it("una revisió antiga del format anterior s'obre i ho diu, sense forçar res", async () => {
    mockHistory();
    const { container } = renderSection();
    await flush();
    const rows = [...container.querySelectorAll("ol li")] as HTMLElement[];
    const old = rows.find((row) => row.textContent?.includes("Revisió 1"))!;
    await act(async () => { fireEvent.click(within(old).getByRole("button", { name: "Veure el manifest" })); });
    await flush();
    const dialog = screen.getAllByRole("dialog").at(-1)!;
    expect(manifestCalls()).toEqual(["/companies/c1/workspaces/w1/packages/p1/manifest"]);
    expect(dialog.textContent).toContain("Manifest de la revisió 1");
    expect(dialog.textContent).toContain("Format anterior");
    // Continua aprovada i ningú no li demana que es recongeli.
    expect(container.textContent).toContain("Anna Puig");
    expect(dialog.textContent).not.toContain("cal congelar");
  });

  it("obrir-ne una altra no reutilitza el manifest anterior", async () => {
    mockHistory();
    const { container } = renderSection();
    await flush();
    const rows = () => [...container.querySelectorAll("ol li")] as HTMLElement[];
    const open = async (label: string) => {
      const row = rows().find((item) => item.textContent?.includes(label))!;
      await act(async () => { fireEvent.click(within(row).getByRole("button", { name: "Veure el manifest" })); });
      await flush();
    };

    await open("Revisió 1");
    let dialog = screen.getAllByRole("dialog").at(-1)!;
    expect(dialog.textContent).toContain("Format anterior");
    expect(dialog.textContent).not.toContain("Commutació d'accés");

    // «Tanca» hi és dues vegades: l'aspa del capçal i el botó del peu.
    await act(async () => { fireEvent.click(within(dialog).getAllByRole("button", { name: "Tanca" }).at(-1)!); });
    await flush();

    await open("Revisió 2");
    dialog = screen.getAllByRole("dialog").at(-1)!;
    expect(dialog.textContent).toContain("Manifest de la revisió 2");
    expect(dialog.textContent).toContain("Format actual");
    expect(dialog.textContent).toContain("Commutació d'accés amb redundància");
    expect(dialog.textContent).not.toContain("Format anterior");
    expect(manifestCalls()).toEqual([
      "/companies/c1/workspaces/w1/packages/p1/manifest",
      "/companies/c1/workspaces/w1/packages/p2/manifest",
    ]);
  });
});

describe("Tancar · estats que aquesta versió no coneix", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("un estat de paquet desconegut es mostra amb el seu codi i no passa per aprovat", async () => {
    const unknown = { ...packageDetailFixture(), status: "una_cosa_nova" } as unknown as PackageDetail;
    const summary = { ...packageSummaryFixture(), status: "una_cosa_nova" } as unknown as PackageSummary;
    mockLoad([summary], { p1: unknown });
    const { container } = renderSection();
    await flush();
    // Es diu el que és, no una pastilla muda.
    expect(container.textContent).toContain("una_cosa_nova");
    // I la pastilla d'aquell estat queda neutra, mai en «a punt».
    const pill = [...container.querySelectorAll("[data-level]")].find((node) => node.textContent === "una_cosa_nova")!;
    expect(pill.getAttribute("data-level")).toBe("neutral");
    expect(container.textContent).not.toContain("Aprovat per presentar");
  });
});

describe("Tancar · qui pot congelar", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("un rol autoritzat pot congelar", async () => {
    // `can_decide` el calcula el servidor com `role in DECISION_ROLES`, que és
    // exactament el que `freeze_package` exigeix amb `assert_can_decide`.
    mockLoad([], {});
    renderSection({ can_decide: true });
    await flush();
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("un rol no autoritzat no pot congelar, i se li diu qui sí que pot", async () => {
    mockLoad([], {});
    const { container } = renderSection({ can_decide: false });
    await flush();
    const freeze = screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement;
    expect(freeze.disabled).toBe(true);
    expect(container.textContent).toMatch(/Només qui té el rol de .* pot congelar/);
    await act(async () => { fireEvent.click(freeze); });
    expect(post).not.toHaveBeenCalled();
  });

  it("estar a punt no concedeix el permís per si sol", async () => {
    mockLoad([], {});
    readiness = readinessFixture();
    renderSection({ can_decide: false });
    await flush();
    expect(readiness.ready_for_final_review).toBe(true);
    expect((screen.getByRole("button", { name: "Congela la proposta" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("Tancar · la cobertura del dossier diu el que compta", () => {
  beforeEach(() => {
    readiness = readinessFixture();
    get.mockReset();
    post.mockReset();
    onChange.mockClear();
  });

  it("el numerador i el denominador són tots dos d'obligatoris", async () => {
    mockLoad([], {});
    // `covered` només compta requisits obligatoris (`summarise_coverage`);
    // els opcionals van a `optional_covered` i no han d'entrar a la xifra.
    const { container } = renderSection({
      dossier_coverage: {
        mandatory_total: 24,
        covered: 20,
        missing_document: 3,
        needs_review: 1,
        not_applicable: 0,
        expired_documents: 0,
        optional_total: 7,
        optional_covered: 5,
      },
    });
    await flush();
    expect(container.textContent).toContain("20 de 24 requisits obligatoris coberts");
    // Ni el total d'opcionals ni la suma dels dos coberts apareixen mai.
    expect(container.textContent).not.toContain("25 de 24");
    expect(container.textContent).not.toContain("de 31");
  });

  it("sense cobertura, la xifra s'omet en comptes d'inventar-se-la", async () => {
    mockLoad([], {});
    const { container } = renderSection({ dossier_coverage: null });
    await flush();
    expect(screen.getByText("Preparació completa")).toBeTruthy();
    expect(container.textContent).not.toContain("requisits obligatoris coberts");
  });
});
