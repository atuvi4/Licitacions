import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { CanonicalRelation, CanonicalRequirement } from "@/lib/review";
import { awardAll, awardScoring, reviewOverview, reviewRequirements } from "@/test/review-fixture";

const get = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args) } };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { id: "m1", company_id: "c1", role: "technical", is_default: true },
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
const PLEC = "Subscripció de pòlissa d'assegurança de responsabilitat civil";
const INFORME = "Pòlissa d'assegurança de responsabilitat civil";
const BIS = "Pòlissa d'assegurança de responsabilitat civil obligatòria";

function relation(
  id: string,
  direction: "outgoing" | "incoming",
  type: string,
  label: string,
  other: string,
  title: string,
): CanonicalRelation {
  return {
    relation_id: id,
    direction,
    relation_type: type,
    relation_label: label,
    other_item_id: other,
    other_item_title: title,
    note: null,
    foldable: type === "duplicate_of",
    created_at: "2026-09-28T13:00:00Z",
  };
}

const base = reviewRequirements.find((item) => item.canonical_id === "r-solvency") as CanonicalRequirement;
const insurance = (id: string, title: string, patch: Partial<CanonicalRequirement>): CanonicalRequirement => ({
  ...base,
  canonical_id: id,
  title,
  category: "insurance",
  category_label: "Assegurança",
  obligation_label: "Obligatori",
  verification_status: "needs_review",
  verification_label: "Cal revisar",
  values: [],
  visible_relations: [],
  folded_relations: [],
  editable: { ...base.editable, title, category: "insurance", values: [] },
  ...patch,
});

const items = [
  ...reviewRequirements,
  insurance("i-plec", PLEC, {
    provenance: "automated_corrected",
    provenance_label: "Extret i corregit",
    values: [{ key: "reviewer_value_1", operator: ">=", value: 500000, unit_label: "EUR", source_label: "Corregit pel revisor", status_label: "Corregit pel revisor", source: "reviewer" }],
    visible_relations: [relation("r1", "incoming", "restatement_of", "Reiterat a", "i-informe", INFORME)],
  }),
  insurance("i-informe", INFORME, {
    visible_relations: [relation("r1", "outgoing", "restatement_of", "També apareix com", "i-plec", PLEC)],
    folded_relations: [relation("r2", "incoming", "duplicate_of", "Té un duplicat", "i-bis", BIS)],
  }),
  insurance("i-bis", BIS, {
    folded_relations: [relation("r2", "outgoing", "duplicate_of", "Duplicat de", "i-informe", INFORME)],
  }),
];

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function renderWith(list: CanonicalRequirement[]) {
  const routes: Record<string, unknown> = {
    "/tenders/t1": { title: "Palamós" },
    [`${REVIEW}/overview`]: reviewOverview,
    [`${REVIEW}/requirements`]: list,
    [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
    [`${REVIEW}/award-criteria?view=all`]: awardAll,
  };
  get.mockImplementation((path: string) =>
    path in routes
      ? Promise.resolve(routes[path])
      : path.endsWith("/history")
        ? Promise.resolve([])
        : Promise.reject(new ApiError(404, "not_found", "No trobat")),
  );
  render(<TenderReviewPage />);
  await flush();
  await flush();
}

const list = () => screen.getByRole("list", { name: "Requisits" });
const row = (title: string) => within(list()).queryByRole("button", { name: new RegExp(`^${title.replace(/[()]/g, "\\$&")}`) });

describe("Duplicats i reiteracions a la llista", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("per defecte, el duplicat d'extracció es plega i la reiteració es veu com a tal", async () => {
    await renderWith(items);

    expect(screen.getByText("Mostrant 10 de 11 · 1 duplicat plegat")).toBeTruthy();
    expect(row(BIS)).toBeNull();
    expect(within(row(INFORME) as HTMLElement).getByText(`Repeteix «${PLEC}»`)).toBeTruthy();
    const canonical = row(PLEC) as HTMLElement;
    expect(within(canonical).getByText("≥ 500.000 EUR")).toBeTruthy();
    expect(within(canonical).getByText("Extret i corregit")).toBeTruthy();
    expect(within(canonical).getByText("Cal revisar")).toBeTruthy();
    // Tres files d'extracció, dues obligacions visibles i cap d'elles sembla independent de l'altra.
    const titles = within(list()).getAllByRole("button").map((b) => b.querySelector("span")?.textContent ?? "");
    expect(titles.filter((t) => t.includes("responsabilitat civil"))).toEqual([PLEC, INFORME]);
  });

  it("el duplicat es pot tornar a veure per auditar-lo", async () => {
    await renderWith(items);

    fireEvent.click(screen.getByRole("button", { name: "Mostra també els duplicats (1)" }));

    expect(screen.getByText("Mostrant 11 de 11")).toBeTruthy();
    expect(within(row(BIS) as HTMLElement).getByText(`Duplicat de «${INFORME}»`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Amaga els duplicats" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("el detall arriba al duplicat plegat i en diu la relació", async () => {
    await renderWith(items);

    fireEvent.click(row(INFORME) as HTMLElement);
    await flush();
    const dialog = screen.getByRole("dialog", { name: INFORME });
    expect(within(dialog).getByText("També apareix com:")).toBeTruthy();
    expect(within(dialog).getByText("Té un duplicat:")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: BIS }));
    await flush();

    const duplicate = screen.getByRole("dialog", { name: BIS });
    expect(within(duplicate).getByText("Duplicat de:")).toBeTruthy();
  });

  it("els filtres compten el que es veu", async () => {
    await renderWith(items);

    const category = screen.getByLabelText("Categoria") as HTMLSelectElement;
    expect([...category.options].map((o) => o.text)).toContain("Assegurança (2)");
    fireEvent.click(screen.getByRole("button", { name: "Mostra també els duplicats (1)" }));
    expect([...(screen.getByLabelText("Categoria") as HTMLSelectElement).options].map((o) => o.text)).toContain(
      "Assegurança (3)",
    );
  });

  it("sense duplicats, no hi ha res a plegar ni cap botó", async () => {
    await renderWith(reviewRequirements);

    expect(screen.getByText("Mostrant 8 de 8")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /duplicats/ })).toBeNull();
  });
});
