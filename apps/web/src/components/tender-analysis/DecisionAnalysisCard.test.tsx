import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture } from "@/test/analysis-fixture";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});

import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/labels";

import { DecisionAnalysisCard } from "./DecisionAnalysisCard";

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => { get.mockReset(); post.mockReset(); });

describe("Targeta d'anàlisi al pas Decidir", () => {
  it("vigent: el mateix resum factual i l'enllaç; mai recalcula", async () => {
    get.mockResolvedValue(analysisFixture());
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByRole("region", { name: "Resum de l'anàlisi" })).toBeTruthy();
    expect(screen.getByText(`Feta el ${formatDateTime(analysisFixture().created_at)}.`)).toBeTruthy();
    expect(screen.getByText("1 element marcat com a «Cal revisar».")).toBeTruthy();
    expect(screen.getByText(`${analysisFixture().findings.length} elements a resoldre abans de decidir.`)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure l'anàlisi" }).getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
    expect(screen.queryByRole("button", { name: /Recalcula|Analitza/ })).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("desactualitzada: etiqueta amb text i enllaç a recalcular a la pestanya", async () => {
    get.mockResolvedValue(analysisFixture({ stale: true }));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("Desactualitzada")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Revisa i recalcula a la pestanya Anàlisi" })).toBeTruthy();
  });

  it("sense anàlisi (404): estat normal; error: nota neutra; mai bloqueja", async () => {
    get.mockRejectedValue(new ApiError(404, "not_found", "x"));
    const { unmount } = render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("Encara no hi ha anàlisi d'aquesta licitació.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure l'anàlisi" }).getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
    unmount();
    get.mockRejectedValue(new ApiError(500, "error", "boom"));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("No s'ha pogut carregar l'anàlisi.")).toBeTruthy();
    expect(screen.queryByText("boom")).toBeNull();
    expect(screen.getByRole("link", { name: "Veure l'anàlisi" }).getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
  });

  it("sense elements pendents: la mateixa frase que la pestanya", async () => {
    get.mockResolvedValue(analysisFixture({ findings: [] }));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("No hi ha cap element pendent de resoldre en aquesta anàlisi.")).toBeTruthy();
    expect(screen.queryByText(/0 elements/)).toBeNull();
  });

  it("un sol element: forma singular", async () => {
    get.mockResolvedValue(analysisFixture({ findings: analysisFixture().findings.slice(0, 1) }));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("1 element a resoldre abans de decidir.")).toBeTruthy();
  });
});
