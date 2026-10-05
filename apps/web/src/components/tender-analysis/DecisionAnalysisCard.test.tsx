import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture } from "@/test/analysis-fixture";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});

import type { DecisionAnalysisState } from "@/lib/analysis";
import { formatDateTime } from "@/lib/labels";

import { DecisionAnalysisCard } from "./DecisionAnalysisCard";

beforeEach(() => { get.mockReset(); post.mockReset(); });

const current = (overrides = {}): DecisionAnalysisState => ({ kind: "current", run: analysisFixture(overrides) });
const link = () => screen.getByRole("link", { name: "Veure l'anàlisi" });

describe("Targeta d'anàlisi al pas Decidir (estat per props)", () => {
  it("vigent: el mateix resum factual i l'enllaç; mai recalcula ni llegeix", () => {
    render(<DecisionAnalysisCard state={current()} tenderId="t1" />);
    expect(screen.getByRole("region", { name: "Resum de l'anàlisi" })).toBeTruthy();
    expect(screen.getByText(`Feta el ${formatDateTime(analysisFixture().created_at)}.`)).toBeTruthy();
    expect(screen.getByText("1 element marcat com a «Cal revisar».")).toBeTruthy();
    expect(screen.getByText(`${analysisFixture().findings.length} elements a resoldre abans de decidir.`)).toBeTruthy();
    expect(link().getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
    expect(screen.queryByText("Desactualitzada")).toBeNull();
    expect(screen.queryByRole("button", { name: /Recalcula|Analitza/ })).toBeNull();
    expect(get).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("no mostra cap nombre d'elements d'abast; manté el recompte de findings", () => {
    const { container } = render(<DecisionAnalysisCard state={current()} tenderId="t1" />);
    expect(container.textContent).not.toMatch(/abast/i);
    expect(screen.getByText(`${analysisFixture().findings.length} elements a resoldre abans de decidir.`)).toBeTruthy();
  });

  it("desactualitzada: etiqueta amb text i enllaç a recalcular a la pestanya", () => {
    render(<DecisionAnalysisCard state={{ kind: "stale", run: analysisFixture({ stale: true }) }} tenderId="t1" />);
    expect(screen.getByText("Desactualitzada")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Revisa i recalcula a la pestanya Anàlisi" })).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("absent (404): estat normal amb l'enllaç", () => {
    render(<DecisionAnalysisCard state={{ kind: "absent" }} tenderId="t1" />);
    expect(screen.getByText("Encara no hi ha anàlisi d'aquesta licitació.")).toBeTruthy();
    expect(link().getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
  });

  it("error: nota neutra, mai bloqueja", () => {
    render(<DecisionAnalysisCard state={{ kind: "error" }} tenderId="t1" />);
    expect(screen.getByText("No s'ha pogut carregar l'anàlisi.")).toBeTruthy();
    expect(link().getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
  });

  it("carregant: cos buit (com abans) però l'enllaç hi és", () => {
    render(<DecisionAnalysisCard state={{ kind: "loading" }} tenderId="t1" />);
    expect(screen.queryByText(/Encara no hi ha anàlisi|No s'ha pogut/)).toBeNull();
    expect(link().getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
    expect(post).not.toHaveBeenCalled();
  });

  it("sense elements pendents: la mateixa frase que la pestanya", () => {
    render(<DecisionAnalysisCard state={current({ findings: [] })} tenderId="t1" />);
    expect(screen.getByText("No hi ha cap element pendent de resoldre en aquesta anàlisi.")).toBeTruthy();
    expect(screen.queryByText(/0 elements/)).toBeNull();
  });

  it("un sol element: forma singular", () => {
    render(<DecisionAnalysisCard state={current({ findings: analysisFixture().findings.slice(0, 1) })} tenderId="t1" />);
    expect(screen.getByText("1 element a resoldre abans de decidir.")).toBeTruthy();
  });
});
