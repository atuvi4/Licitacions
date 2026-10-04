import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { tabFromParam } from "@/app/(app)/licitacions/[tenderId]/header";
import { GONE_REQUIREMENT_ID, analysisFixture, requirementsFixture, structureFixture } from "@/test/analysis-fixture";

const get = vi.fn();
const post = vi.fn();
let role: string | undefined = "owner";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1", membership: role ? { role } : undefined }) }));
vi.mock("@/components/WorkspaceLauncher", () => ({ WorkspaceLauncher: () => <button type="button">Obre la candidatura</button> }));

import { ApiError } from "@/lib/api";

import { AnalysisTab } from "./AnalysisTab";

const BASE = "/companies/c1/tenders/t1/analysis";
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

function respond(current: unknown) {
  get.mockImplementation((path: string) => {
    if (path === `${BASE}/current`) return current instanceof Error ? Promise.reject(current) : Promise.resolve(current);
    if (path === "/companies/c1/tenders/t1/structure") return Promise.resolve(structureFixture);
    return Promise.resolve(undefined);
  });
}

async function renderTab() {
  render(<AnalysisTab companyId="c1" tenderId="t1" requirements={requirementsFixture} workspaceId={null} candidatureChecked />);
  await flush();
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  role = "owner";
});

describe("Pestanya Anàlisi: vies d'execució i abast (T9)", () => {
  it("diu «Vies d'execució (candidats)» i els elements d'abast de l'estructura", async () => {
    respond(analysisFixture());
    await renderTab();
    expect(screen.getByText("Vies d'execució (candidats)")).toBeTruthy();
    expect(screen.getByText("disponibles 1 · pendents 1 · no disponibles 0", { exact: false })).toBeTruthy();
    expect(screen.getByText("2 elements d'abast al plec")).toBeTruthy();
  });

  it("sense estructura carregada, el comptador d'abast no surt", async () => {
    get.mockImplementation((path: string) => {
      if (path === `${BASE}/current`) return Promise.resolve(analysisFixture());
      if (path === "/companies/c1/tenders/t1/structure") return Promise.reject(new ApiError(500, "error", "x"));
      return Promise.resolve(undefined);
    });
    await renderTab();
    expect(screen.getByText("Vies d'execució (candidats)")).toBeTruthy();
    expect(screen.queryByText(/elements? d'abast al plec/)).toBeNull();
  });
});

describe("Pestanya Anàlisi: estats", () => {
  it("sense anàlisi (404) és l'estat normal, amb què és i què no és, i el botó Analitza", async () => {
    respond(new ApiError(404, "not_found", "Cap analisi vigent."));
    await renderTab();
    expect(screen.getByText(/Encara no hi ha cap anàlisi/)).toBeTruthy();
    expect(screen.getByText(/no decideix per tu/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Analitza" })).toBeTruthy();
    expect(screen.queryByText(/No s'ha pogut/)).toBeNull();
  });

  it("sense permís, el botó es veu desactivat amb qui ho pot fer", async () => {
    role = "viewer";
    respond(new ApiError(404, "not_found", "x"));
    await renderTab();
    expect((screen.getByRole("button", { name: "Analitza" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Només qui té el rol/)).toBeTruthy();
  });

  it("409 de prerequisits sense anàlisi: explica què falta a partir del codi", async () => {
    respond(new ApiError(404, "not_found", "x"));
    post.mockRejectedValue(new ApiError(409, "analysis_prerequisites_missing", "No hi ha requisits.", [], { missing: ["requirements"] }));
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Analitza" }));
    await flush();
    expect(screen.getByText(/Encara no hi ha requisits per analitzar/)).toBeTruthy();
    expect(screen.queryByText("No hi ha requisits.")).toBeNull();
    expectRequirementsLink();
  });

  it("409 amb una anàlisi desactualitzada: l'antiga continua visible i el motiu surt al costat de Recalcula", async () => {
    respond(analysisFixture({ stale: true }));
    post.mockRejectedValue(new ApiError(409, "analysis_prerequisites_missing", "x", [], { missing: ["requirements"] }));
    await renderTab();
    expect(screen.getByText("Desactualitzada")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(screen.getByText(/No es pot recalcular fins que es resolgui/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Què cal resoldre abans de decidir" })).toBeTruthy();
    expectRequirementsLink();
  });

  it("recalcular: un sol POST encara que es cliqui dues vegades, i anuncia si és la mateixa", async () => {
    respond(analysisFixture({ stale: true }));
    let resolvePost: (value: unknown) => void = () => {};
    post.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));
    await renderTab();
    const button = screen.getByRole("button", { name: "Recalcula" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${BASE}/runs`, {});
    await act(async () => resolvePost({ ...analysisFixture(), reused: true }));
    expect(screen.getByRole("status", { name: "Estat del recàlcul" }).textContent).toContain(
      "Les dades no han canviat: és la mateixa anàlisi.",
    );
  });

  it("reused amb un altre id (p. ex. ja recalculada des d'un altre lloc) anuncia «actualitzada», no «la mateixa»", async () => {
    respond(analysisFixture({ stale: true }));
    post.mockResolvedValue({ ...analysisFixture({ id: "run-2" }), reused: true });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(screen.getByRole("status", { name: "Estat del recàlcul" }).textContent).toBe("Anàlisi actualitzada.");
  });

  it("conflicte concurrent i errors: text humà, mai JSON ni codi", async () => {
    respond(analysisFixture());
    post.mockRejectedValue(new ApiError(409, "analysis_run_conflict", "x"));
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(within(screen.getByRole("alert")).getByText(/S'ha recalculat alhora des d'un altre lloc/)).toBeTruthy();
  });

  it("resum factual sense score ni percentatge, i cap codi ni UUID visible", async () => {
    respond(analysisFixture());
    const { container } = render(<AnalysisTab companyId="c1" tenderId="t1" requirements={requirementsFixture} workspaceId={null} candidatureChecked />);
    await flush();
    const summary = screen.getByRole("region", { name: "Resum de l'anàlisi" });
    expect(within(summary).getByText(/No se sap: 2/)).toBeTruthy();
    expect(summary.textContent).toMatch(/2 requisits no es poden avaluar/);
    // El peu de decisió diu explícitament «Tuvi no recomana sí ni no»: és l'única aparició permesa de «recomana».
    const text = (container.textContent ?? "").replace("Tuvi no recomana sí ni no", "");
    expect(text).not.toMatch(/%|score|recomana|[0-9a-f]{8}-[0-9a-f]{4}-|FACT_|_v1|run-1/i);
    expect(text).not.toContain(GONE_REQUIREMENT_ID);
  });
});

/** El 409 `requirements` porta a la pestanya Requisits de la mateixa licitació, amb el paràmetre que llegeix la pàgina. */
function expectRequirementsLink() {
  const link = screen.getByRole("link", { name: "Obre la pestanya Requisits extrets" });
  const href = link.getAttribute("href") as string;
  expect(href).toBe("/licitacions/t1?tab=requisits");
  expect(tabFromParam(new URL(href, "http://localhost").searchParams.get("tab"))).toBe("requisits");
}

describe("Pestanya Anàlisi: focus del recàlcul", () => {
  it("mentre recalcula, el botó té aria-busy i aria-disabled (mai disabled natiu) i conserva el focus", async () => {
    respond(analysisFixture({ stale: true }));
    let resolvePost: (value: unknown) => void = () => {};
    post.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));
    await renderTab();
    const button = screen.getByRole("button", { name: "Recalcula" }) as HTMLButtonElement;
    button.focus();
    fireEvent.click(button);
    expect(button.getAttribute("aria-busy")).toBe("true");
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.disabled).toBe(false);
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    expect(post).toHaveBeenCalledTimes(1);
    await act(async () => resolvePost({ ...analysisFixture({ id: "run-2" }), reused: false }));
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-disabled")).not.toBe("true");
  });

  it("el primer «Analitza» deixa el focus al botó «Recalcula» que el substitueix, mai al body", async () => {
    respond(new ApiError(404, "not_found", "x"));
    post.mockResolvedValue({ ...analysisFixture(), reused: false });
    await renderTab();
    const analyse = screen.getByRole("button", { name: "Analitza" });
    analyse.focus();
    fireEvent.click(analyse);
    await flush();
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Recalcula" }));
  });
});

describe("Pestanya Anàlisi: càrrega, errors i permís", () => {
  it("carregant: esquelet, sense afirmar cap error ni l'estat buit", async () => {
    get.mockImplementation(() => new Promise(() => {}));
    const { container } = render(<AnalysisTab companyId="c1" tenderId="t1" requirements={null} workspaceId={null} candidatureChecked={false} />);
    await flush();
    expect(container.querySelector(".animate-skeleton")).toBeTruthy();
    expect(screen.queryByText(/No s'ha pogut/)).toBeNull();
    expect(screen.queryByText(/Encara no hi ha cap anàlisi/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Analitza|Recalcula/ })).toBeNull();
  });

  it("error de lectura (no 404): avís amb reintent, sense el missatge del servidor ni l'estat buit", async () => {
    respond(new ApiError(500, "internal_error", "boom"));
    await renderTab();
    expect(screen.getByText("No s'ha pogut carregar l'anàlisi.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Torna-ho a provar" })).toBeTruthy();
    expect(screen.queryByText("boom")).toBeNull();
    expect(screen.queryByText(/Encara no hi ha cap anàlisi/)).toBeNull();
  });

  it("POST 403: nota de permís amb qui ho pot fer, no un error vermell; el focus es queda al botó", async () => {
    respond(analysisFixture());
    post.mockRejectedValue(new ApiError(403, "permission_denied", "Forbidden"));
    await renderTab();
    const button = screen.getByRole("button", { name: "Recalcula" });
    button.focus();
    fireEvent.click(button);
    await flush();
    expect(screen.getByText(/^Només qui té el rol de .+ pot analitzar o recalcular l'anàlisi\.$/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Forbidden")).toBeNull();
    const after = screen.getByRole("button", { name: "Recalcula" });
    expect(after.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(after);
    expect(screen.getByRole("status", { name: "Estat del recàlcul" }).textContent).toMatch(/El teu rol no pot recalcular/);
  });

  it("mentre les dades complementàries es carreguen no diu que han fallat", async () => {
    get.mockImplementation((path: string) =>
      path === `${BASE}/current` ? Promise.resolve(analysisFixture({ delivery_candidates: [] })) : new Promise(() => {}),
    );
    render(<AnalysisTab companyId="c1" tenderId="t1" requirements={null} workspaceId={null} candidatureChecked />);
    await flush();
    expect(screen.getByRole("heading", { name: "Anàlisi del plec" })).toBeTruthy();
    expect(screen.queryByText(/no s'han pogut carregar/)).toBeNull();
    expect(screen.queryByText("No s'ha pogut carregar l'estructura del plec.")).toBeNull();
  });

  it("amb un error real de les dades complementàries, sí que ho diu", async () => {
    get.mockImplementation((path: string) =>
      path === `${BASE}/current` ? Promise.resolve(analysisFixture({ delivery_candidates: [] })) : Promise.reject(new ApiError(500, "internal_error", "boom")),
    );
    render(<AnalysisTab companyId="c1" tenderId="t1" requirements={null} requirementsFailed workspaceId={null} candidatureChecked />);
    await flush();
    expect(screen.getByText(/Algunes dades complementàries no s'han pogut carregar/)).toBeTruthy();
    expect(screen.getByText("No s'ha pogut carregar l'estructura del plec.")).toBeTruthy();
  });
});
