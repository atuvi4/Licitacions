import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PackageDetail, PackageSummary } from "@/lib/workspace";
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

import { SubmissionPackageSection } from "./SubmissionPackage";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onChange = vi.fn(async () => undefined);

/** Simula GET …/packages i GET …/packages/{id} amb el que hi hagi a cada moment. */
function mockLoad(list: PackageSummary[], details: Record<string, PackageDetail>) {
  get.mockImplementation(async (path: string) => {
    if (path.endsWith("/packages")) return list;
    const id = path.split("/").pop()!;
    return details[id];
  });
}

function renderSection(overrides: Parameters<typeof workspaceFixture>[0] = {}) {
  return render(
    <SubmissionPackageSection
      workspace={workspaceFixture({ status: "ready_for_final_review", readiness: "ready_for_final_review", readiness_blockers: [], ...overrides })}
      onChange={onChange}
      signerName="Anna Puig"
    />,
  );
}

const approveButton = () => screen.getByRole("button", { name: "Aprova per presentar" }) as HTMLButtonElement;
const dialog = () => screen.getByRole("dialog");
const dialogSubmit = () => [...dialog().querySelectorAll("button")].find((b) => b.textContent?.trim() === "Aprova per presentar")! as HTMLButtonElement;

describe("SubmissionPackageSection · congelar, revisar, aprovar; aprovar no és presentar", () => {
  beforeEach(() => {
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
    expect(container.textContent).toContain("El manifest es calcula sobre els bytes, no sobre els noms.");
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
    expect(banner.textContent).toContain("Els documents del paquet ja no són els que hi ha ara.");
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
    expect(dialog().textContent).toContain("Els documents del paquet ja no són els que hi ha ara.");
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

  it("buit i dossier no a punt: acció desactivada i bàner d'atenció amb els blocadors (no és un problema de permisos)", async () => {
    mockLoad([], {});
    const { container } = renderSection({ status: "preparing_bid", readiness: "incomplete", readiness_blockers: ["mandatory_evidence_missing"] });
    await flush();
    expect(screen.getByText("Encara no s'ha congelat cap paquet")).toBeTruthy();
    const freeze = screen.getByRole("button", { name: "Congela el paquet" }) as HTMLButtonElement;
    expect(freeze.disabled).toBe(true);
    const banner = container.querySelector("[role='status'][data-level='attention']")!;
    expect(banner.textContent).toContain("Falten documents per a requisits obligatoris.");
    expect(container.textContent).not.toMatch(/Només .* pot/);
    expect(container.textContent).toContain("Estat: Preparant oferta");
    expect(container.textContent).toContain("encara no s'ha presentat res");
  });

  it("buit i a punt: congelar crea la revisió 1; un freeze fallit no substitueix res i s'explica", async () => {
    mockLoad([], {});
    post.mockRejectedValueOnce(new ApiError(409, "package_not_ready", "El dossier encara no esta a punt per congelar-lo.", [], { blockers: ["mandatory_tasks_open"] }));
    const { container } = renderSection({ status: "preparing_bid" });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Congela el paquet" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/packages", {});
    expect(screen.getByText("No s'ha pogut congelar la revisió")).toBeTruthy();
    expect(container.textContent).toContain("Hi ha tasques obligatòries pendents.");
    expect(screen.getByText("Encara no s'ha congelat cap paquet")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();

    post.mockResolvedValueOnce({ created: true, package: packageDetailFixture() });
    mockLoad([packageSummaryFixture()], { p1: packageDetailFixture() });
    fireEvent.click(screen.getByRole("button", { name: "Congela el paquet" }));
    await flush();
    expect(screen.getByText("Revisió 1 del paquet")).toBeTruthy();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("sense el rol per congelar: botó visible i desactivat amb PermissionNote; error de càrrega amb reintent", async () => {
    mockLoad([], {});
    const first = renderSection({ status: "preparing_bid", can_decide: false });
    await flush();
    expect((screen.getByRole("button", { name: "Congela el paquet" }) as HTMLButtonElement).disabled).toBe(true);
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
