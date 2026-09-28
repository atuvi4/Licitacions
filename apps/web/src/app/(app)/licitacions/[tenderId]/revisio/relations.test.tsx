import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { CanonicalRelation, CanonicalRequirement } from "@/lib/review";
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
const QA = "[QA] Requisit manual de prova";
const QA_OTHER = "[QA] Requisit relacionat de prova";
const qa = (id: string, title: string): CanonicalRequirement => ({
  ...reviewRequirements[0],
  canonical_id: id,
  title,
  values: [],
  evidence: [],
  visible_relations: [],
  folded_relations: [],
  editable: { ...reviewRequirements[0].editable, title, values: [] },
});
const BASE = [...reviewRequirements, qa("qa-1", QA), qa("qa-2", QA_OTHER)];

function relation(type: string, direction: "outgoing" | "incoming", label: string, other: string, title: string): CanonicalRelation {
  return {
    relation_id: `rel-${type}`,
    direction,
    relation_type: type,
    relation_label: label,
    other_item_id: other,
    other_item_title: title,
    note: null,
    foldable: type === "duplicate_of",
    created_at: "2026-09-28T15:00:00Z",
  };
}

/** La llista tal com la tornaria el servidor després de crear `type` de qa-1 cap a qa-2. */
function afterCreating(type: string, outgoing: string, incoming: string): CanonicalRequirement[] {
  const folded = type === "duplicate_of";
  return BASE.map((item) => {
    if (item.canonical_id === "qa-1") {
      const r = relation(type, "outgoing", outgoing, "qa-2", QA_OTHER);
      return folded ? { ...item, folded_relations: [r] } : { ...item, visible_relations: [r] };
    }
    if (item.canonical_id === "qa-2") {
      const r = relation(type, "incoming", incoming, "qa-1", QA);
      return folded ? { ...item, folded_relations: [r] } : { ...item, visible_relations: [r] };
    }
    return item;
  });
}

let lists: CanonicalRequirement[][];

function respond() {
  lists = [BASE];
  get.mockImplementation((path: string) => {
    if (path === `${REVIEW}/requirements`) return Promise.resolve(lists.length > 1 ? lists.shift() : lists[0]);
    const routes: Record<string, unknown> = {
      "/tenders/t1": { title: "Palamós" },
      [`${REVIEW}/overview`]: reviewOverview,
      [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
      [`${REVIEW}/award-criteria?view=all`]: awardAll,
    };
    if (path in routes) return Promise.resolve(routes[path]);
    if (path.endsWith("/history")) return Promise.resolve([]);
    return Promise.reject(new ApiError(404, "not_found", "No trobat"));
  });
}

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const list = () => screen.getByRole("list", { name: "Requisits" });
const row = (title: string) =>
  within(list()).queryByRole("button", { name: new RegExp(`^${title.replace(/[[\]()]/g, "\\$&")}`) });
const calls = (path: string) => get.mock.calls.filter(([p]) => p === path).length;

async function openRelating(title = QA) {
  render(<TenderReviewPage />);
  await flush();
  await flush();
  fireEvent.click(row(title) as HTMLElement);
  await flush();
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Relacionar" }));
  await flush();
  return { dialog, editor: within(dialog).getByRole("group", { name: "Relacionar amb un altre requisit" }) };
}

function chooseTarget(editor: HTMLElement, query: string, title: string) {
  fireEvent.change(within(editor).getByLabelText(/^Cerca el requisit/), { target: { value: query } });
  fireEvent.click(within(within(editor).getByRole("list", { name: "Requisits trobats" })).getByRole("button", { name: new RegExp(`^${title.replace(/[[\]()]/g, "\\$&")}`) }));
}

describe("Relacions entre requisits", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    role = "technical";
    respond();
  });

  it("un rol de consulta veu les relacions però no les pot crear ni treure", async () => {
    role = "viewer";
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(row("Solució cloud d'alta disponibilitat") as HTMLElement);
    await flush();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Cobert parcialment per:")).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "Relacionar" })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Treure relació" })).toBeNull();
  });

  it("el cercador exclou el requisit actual i no ensenya identificadors", async () => {
    const { editor } = await openRelating();

    fireEvent.change(within(editor).getByLabelText(/^Cerca el requisit/), { target: { value: "qa" } });
    const found = within(editor).getByRole("list", { name: "Requisits trobats" });
    const titles = within(found).getAllByRole("button").map((b) => b.querySelector("span")?.textContent);
    expect(titles).toEqual([QA_OTHER]);
    expect(found.textContent).not.toMatch(/qa-2|qa-1/);
    expect(within(found).getByText("Afegit manualment")).toBeTruthy();
    expect(within(found).getByText("Pendent de revisar")).toBeTruthy();
  });

  it("cada tipus s'explica, la direcció es veu abans de desar i no hi ha codis", async () => {
    const { editor } = await openRelating();

    for (const help of [
      "És la mateixa obligació extreta més d'una vegada.",
      "El plec expressa aquesta mateixa obligació en un altre lloc.",
      "Els dos requisits se solapen, però cap no substitueix completament l'altre.",
      "Hi ha relació entre els requisits, però no són equivalents.",
    ]) {
      expect(within(editor).getByText(new RegExp(help.replace(/[.()]/g, "\\$&")))).toBeTruthy();
    }
    expect(editor.textContent).not.toMatch(/duplicate_of|restatement_of|partial_match_of|related_to|parent_of|supersedes/);
    chooseTarget(editor, "relacionat", QA_OTHER);
    fireEvent.click(within(editor).getByLabelText(/Cobert parcialment per/));
    expect(within(editor).getByText(`«${QA}» està cobert parcialment per «${QA_OTHER}».`)).toBeTruthy();
    expect((within(editor).getByRole("button", { name: /Desa la relació/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it.each([
    ["duplicate_of", /^Duplicat de/, "Duplicat de", "Té un duplicat"],
    ["restatement_of", /^També apareix com/, "També apareix com", "Reiterat a"],
    ["partial_match_of", /^Cobert parcialment per/, "Cobert parcialment per", "Cobreix parcialment"],
    ["related_to", /^Relacionat amb/, "Relacionat amb", "Relacionat amb"],
  ])("%s: envia aquest → l'altre i torna a llegir la llista", async (type, radio, outgoing, incoming) => {
    post.mockResolvedValue(relation(type, "outgoing", outgoing, "qa-2", QA_OTHER));
    const { dialog, editor } = await openRelating();
    lists = [afterCreating(type, outgoing, incoming)];
    chooseTarget(editor, "relacionat", QA_OTHER);
    fireEvent.click(within(editor).getByLabelText(radio));
    fireEvent.change(within(editor).getByLabelText(/Nota/), { target: { value: " Prova de QA " } });
    fireEvent.click(within(editor).getByRole("button", { name: /Desa la relació/ }));
    await flush();
    await flush();

    expect(post).toHaveBeenCalledWith(`${REVIEW}/relations`, {
      from_item_id: "qa-1",
      to_item_id: "qa-2",
      relation_type: type,
      note: "Prova de QA",
    });
    expect(within(dialog).getByText(/^Relació desada:/)).toBeTruthy();
    expect(within(dialog).getByText(`${outgoing}:`)).toBeTruthy();
    expect(calls(`${REVIEW}/requirements`)).toBe(2);
    // Una relació no canvia cap recompte ni cap revisió: el resum no es torna a llegir.
    expect(calls(`${REVIEW}/overview`)).toBe(1);
    expect(post.mock.calls[0][1]).not.toHaveProperty("expected_revision");

    if (type === "duplicate_of") {
      // El duplicat es plega; l'altre continua a la llista, i el detall continua obert.
      expect(row(QA)).toBeNull();
      expect(row(QA_OTHER)).toBeTruthy();
      expect(screen.getByText(/1 duplicat plegat/)).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Mostra també els duplicats (1)" }));
      expect(within(row(QA) as HTMLElement).getByText(`Duplicat de «${QA_OTHER}»`)).toBeTruthy();
    } else {
      const hint = { restatement_of: `Repeteix «${QA_OTHER}»`, partial_match_of: `Cobert parcialment per «${QA_OTHER}»`, related_to: `Relacionat amb «${QA_OTHER}»` }[type] as string;
      expect(within(row(QA) as HTMLElement).getByText(hint)).toBeTruthy();
      expect(row(QA_OTHER)).toBeTruthy();
      expect(screen.queryByText(/duplicat plegat/)).toBeNull();
    }
  });

  it("els filtres es mantenen després de relacionar", async () => {
    post.mockResolvedValue(relation("related_to", "outgoing", "Relacionat amb", "qa-2", QA_OTHER));
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.change(screen.getByLabelText("Origen"), { target: { value: "manual" } });
    fireEvent.click(row(QA) as HTMLElement);
    await flush();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Relacionar" }));
    const editor = within(dialog).getByRole("group", { name: "Relacionar amb un altre requisit" });
    lists = [afterCreating("related_to", "Relacionat amb", "Relacionat amb")];
    chooseTarget(editor, "relacionat", QA_OTHER);
    fireEvent.click(within(editor).getByLabelText(/^Relacionat amb/));
    fireEvent.click(within(editor).getByRole("button", { name: /Desa la relació/ }));
    await flush();

    expect((screen.getByLabelText("Origen") as HTMLSelectElement).value).toBe("manual");
  });

  it.each([
    [new ApiError(409, "review_relation_exists", "Aquesta relacio ja existeix."), /ja existeix \(potser en el sentit contrari\)/],
    [new ApiError(422, "validation_error", "Aquesta relacio tancaria un cicle."), /^Aquesta relacio tancaria un cicle\.$/],
    [new ApiError(403, "forbidden", "x"), /^No tens permisos per modificar aquesta revisió\.$/],
    [new ApiError(404, "not_found", "x"), /^Un dels requisits ja no està disponible\.$/],
    [new TypeError("Failed to fetch"), /Torna-ho a provar/],
  ])("error %#: una frase i l'editor es queda obert", async (error, message) => {
    post.mockRejectedValue(error);
    const { editor } = await openRelating();
    chooseTarget(editor, "relacionat", QA_OTHER);
    fireEvent.click(within(editor).getByLabelText(/^Relacionat amb/));
    fireEvent.click(within(editor).getByRole("button", { name: /Desa la relació/ }));
    await flush();
    await flush();

    expect(within(editor).getByRole("alert").textContent).toMatch(message);
    expect(post).toHaveBeenCalledTimes(1);
    // Un 409 torna a llegir la llista perquè la relació que ja existeix es vegi.
    const reloads = calls(`${REVIEW}/requirements`);
    expect(reloads).toBe(error instanceof ApiError && error.status === 409 ? 2 : 1);
  });

  it("treure una relació: confirmació, arxiu tou i sense soroll si ja estava treta", async () => {
    lists = [afterCreating("related_to", "Relacionat amb", "Relacionat amb"), BASE];
    post.mockResolvedValueOnce({ changed: true, relation: relation("related_to", "outgoing", "Relacionat amb", "qa-2", QA_OTHER) });
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(row(QA) as HTMLElement);
    await flush();
    const dialog = screen.getByRole("dialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Treure relació" }));
    expect(within(dialog).getByText(/El requisit i el seu historial no s'eliminaran\./)).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Treu la relació" }));
    await flush();
    await flush();

    expect(post).toHaveBeenCalledWith(`${REVIEW}/relations/rel-related_to/archive`, {});
    expect(within(dialog).getByText("Relació treta.")).toBeTruthy();
    expect(within(dialog).queryByText("Relacionat amb:")).toBeNull();
    expect(within(row(QA) as HTMLElement).queryByText(/Relacionat amb/)).toBeNull();
  });

  it("arxivar una relació que ja estava treta no fa soroll", async () => {
    lists = [afterCreating("related_to", "Relacionat amb", "Relacionat amb"), BASE];
    post.mockResolvedValueOnce({ changed: false, relation: relation("related_to", "outgoing", "Relacionat amb", "qa-2", QA_OTHER) });
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(row(QA) as HTMLElement);
    await flush();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Treure relació" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Treu la relació" }));
    await flush();

    expect(within(dialog).getByText("Aquesta relació ja estava treta.")).toBeTruthy();
  });

  it("Escape tanca l'editor de relacions, no el detall, i el focus torna a «Relacionar»", async () => {
    const { dialog, editor } = await openRelating();
    const search = within(editor).getByLabelText(/^Cerca el requisit/);
    expect(document.activeElement).toBe(search);

    fireEvent.keyDown(search, { key: "Escape" });
    await flush();

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dialog).queryByRole("group", { name: "Relacionar amb un altre requisit" })).toBeNull();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Relacionar" }));
  });
});
