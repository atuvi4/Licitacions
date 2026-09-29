import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { awardScoring, reviewOverview } from "@/test/review-fixture";

const get = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args) } };
});

import { ReviewSummaryCard } from "./ReviewSummaryCard";

const REVIEW = "/companies/c1/tenders/t1/review";
const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

function routes(values: Record<string, unknown>) {
  get.mockImplementation((path: string) => {
    if (!(path in values)) return Promise.reject(new ApiError(404, "not_found", "No trobat"));
    const value = values[path];
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  });
}

describe("Resum de la revisió a la fitxa", () => {
  // Amb claus: una fletxa que retorna el mock el faria servir de «teardown» i el cridaria sense ruta.
  beforeEach(() => {
    get.mockReset();
  });

  it("amb revisió: xifres vives de la revisió canònica i una sola crida principal", async () => {
    routes({ [`${REVIEW}/overview`]: reviewOverview, [`${REVIEW}/award-criteria?view=scoring`]: awardScoring });
    render(<ReviewSummaryCard companyId="c1" tenderId="t1" />);
    await flush();
    await flush();

    const metrics = screen.getByLabelText("Resum de la revisió");
    const value = (label: string) => within(metrics).getByText(label).nextElementSibling?.textContent;
    expect(value("Requisits")).toBe(String(reviewOverview.total_requirements));
    expect(value("Cal revisar")).toBe(String(reviewOverview.needs_review_count));
    expect(value("Criteris que puntuen")).toBe("9");
    expect(value("Punts")).toBe("100 punts");
    const links = screen.getAllByRole("link", { name: "Obre la revisió" });
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/licitacions/t1/revisio");
    // Cap escriptura: només lectures (resum i criteris).
    expect(get.mock.calls.map(([path]) => path)).toEqual([
      `${REVIEW}/overview`, `${REVIEW}/award-criteria?view=scoring`,
    ]);
  });

  it("sense revisió iniciada: text genèric i la crida; no inicia res ni llegeix criteris", async () => {
    routes({ [`${REVIEW}/overview`]: { ...reviewOverview, review_started: false, total_requirements: 0 } });
    render(<ReviewSummaryCard companyId="c1" tenderId="t1" />);
    await flush();

    expect(screen.queryByLabelText("Resum de la revisió")).toBeNull();
    expect(screen.getByText(/tal com els revisa la vostra empresa/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Obre la revisió" })).toBeTruthy();
    expect(get.mock.calls.map(([path]) => path)).toEqual([`${REVIEW}/overview`]);
  });

  it("si la lectura falla (404), la fitxa no es trenca: text genèric i la crida", async () => {
    routes({});
    render(<ReviewSummaryCard companyId="c1" tenderId="t1" />);
    await flush();

    expect(screen.getByRole("heading", { name: "Revisió de la licitació" })).toBeTruthy();
    expect(screen.queryByLabelText("Resum de la revisió")).toBeNull();
    expect(screen.getByRole("link", { name: "Obre la revisió" })).toBeTruthy();
  });
});
