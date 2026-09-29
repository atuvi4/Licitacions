import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { awardAll, awardScoring, reviewOverview, reviewRequirements } from "@/test/review-fixture";

const get = vi.fn();
/** Qualsevol escriptura: aquesta pantalla no n'ha de fer cap. */
const write = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const spy = (...args: unknown[]) => write(...args);
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: spy, patch: spy, put: spy, delete: spy, upload: spy },
  };
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

function respondWith(map: Record<string, unknown>) {
  get.mockImplementation((path: string) => {
    if (!(path in map)) return Promise.reject(Object.assign(new Error(`sense resposta per a ${path}`), { status: 500 }));
    const value = map[path];
    if (value instanceof Error) return Promise.reject(value);
    return Promise.resolve(value);
  });
}

function palamos(overrides: Record<string, unknown> = {}) {
  return {
    "/tenders/t1": { title: "Servei de telefonia fixa i mòbil de l'Ajuntament de Palamós" },
    [`${REVIEW}/overview`]: reviewOverview,
    [`${REVIEW}/requirements`]: reviewRequirements,
    [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
    [`${REVIEW}/award-criteria?view=all`]: awardAll,
    ...overrides,
  };
}

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

async function renderPage() {
  render(<TenderReviewPage />);
  await flush();
  await flush();
}

function requested(): string[] {
  return get.mock.calls.map(([path]) => path as string);
}

/** Cap codi intern, en cap lloc del text visible. */
const FORBIDDEN = [
  "mandatory/mandatory",
  "informative/informational",
  "needs_review",
  "claim_not_supported",
  "value_not_in_quote",
  "chunk",
  "normalised",
  "formula_not_evidence_verified",
  "restatement_excluded",
  "partial_match_of",
  "related_to",
  "not_evidence_verified",
  "award_class",
  "run_id",
];

describe("Revisió de la licitació", () => {
  beforeEach(() => {
    get.mockReset();
    write.mockReset();
  });

  it("mostra el resum amb les xifres de l'API", async () => {
    respondWith(palamos());
    await renderPage();

    const summary = screen.getByRole("heading", { name: "Resum" }).parentElement as HTMLElement;
    const stat = (label: string) => within(summary).getByText(label).nextElementSibling?.textContent;
    expect(stat("Requisits")).toBe("257");
    expect(stat("Cal revisar")).toBe("56");
    expect(stat("Criteris que puntuen")).toBe("9");
    expect(stat("Puntuació")).toBe("100");
    expect(screen.getByText("Servei de telefonia fixa i mòbil de l'Ajuntament de Palamós")).toBeTruthy();
    // Només les rutes canòniques de revisió; mai les files d'extracció.
    expect(requested().filter((path) => !path.startsWith(REVIEW) && path !== "/tenders/t1")).toEqual([]);
    expect(write).not.toHaveBeenCalled();
  });

  it("els criteris que puntuen: 9 files, preu 80 i el bloc de 20 amb els seus 8", async () => {
    respondWith(palamos());
    await renderPage();

    const scoring = screen.getByRole("list", { name: "Criteris que puntuen" });
    const block = within(scoring).getByRole("list", { name: "Criteris de Criteris qualitatius" });
    expect(within(block).getAllByRole("listitem")).toHaveLength(8);
    expect(within(scoring).getByText("Criteri preu")).toBeTruthy();
    expect(within(scoring).getByText("80 punts")).toBeTruthy();
    expect(within(scoring).getByText("Criteris qualitatius")).toBeTruthy();
    expect(within(scoring).getByText("20 punts")).toBeTruthy();
    const rows = within(scoring).getAllByText(/^\d+ punts?$/);
    // 80 + 20 del bloc + els 8 criteris del bloc.
    expect(rows).toHaveLength(10);
    expect(screen.getByText("100 punts totals · 9 criteris que puntuen")).toBeTruthy();
    expect(screen.getByText("Total").nextElementSibling?.textContent).toBe("100 punts");
    // Les 23 files no surten per defecte.
    expect(screen.queryByText("Backup 4G/5G per serveis de veu")).toBeNull();
    expect(requested()).not.toContain(`${REVIEW}/award-criteria?view=all`);
  });

  it("l'avís de la fórmula és informatiu, amb el nom del criteri i sense codi", async () => {
    respondWith(palamos());
    await renderPage();

    const warnings = screen.getByRole("heading", { name: "Per tenir en compte" }).parentElement as HTMLElement;
    expect(within(warnings).getByText("Fórmula pendent de verificar")).toBeTruthy();
    expect(within(warnings).getAllByText("Informació:")).toHaveLength(2);
    expect(within(warnings).queryByText("Atenció:")).toBeNull();
    expect(within(warnings).getByText("Criteri preu")).toBeTruthy();
    // També al costat de la fórmula, com a estat i no com a error.
    expect(screen.getByText("Vi = Bi × 80 punts / Max(Bs, Bmax)")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("«Veure estructura completa» mostra les 23 files i el que no compta", async () => {
    respondWith(palamos());
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Veure estructura completa" }));
    await flush();

    expect(requested()).toContain(`${REVIEW}/award-criteria?view=all`);
    const counting = screen.getByRole("heading", { name: "Compten al total" }).closest("section") as HTMLElement;
    expect(within(counting).getAllByRole("listitem")).toHaveLength(9);
    expect(within(counting).getByText("Compta als 100 punts")).toBeTruthy();
    for (const title of ["Totals de bloc", "Repeticions en altres documents", "Model d'oferta", "No puntuen", "Millores valorables"]) {
      const section = screen.getByRole("heading", { name: title }).closest("section") as HTMLElement;
      expect(within(section).getByText("No compta al total")).toBeTruthy();
    }
    expect(screen.getByText("Backup 4G/5G per serveis de veu")).toBeTruthy();
    expect(screen.getAllByText("Repeteix «Criteris qualitatius»")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Mostra només els 9 criteris que sumen punts" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("filtra els requisits per estat, origen, obligació, categoria i text", async () => {
    respondWith(palamos());
    await renderPage();

    const list = () => screen.getByRole("list", { name: "Requisits" });
    expect(within(list()).getAllByRole("listitem")).toHaveLength(8);
    fireEvent.change(screen.getByLabelText("Origen"), { target: { value: "manual" } });
    expect(within(list()).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("Mostrant 3 de 8")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Cerca"), { target: { value: "ddi" } });
    expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Cerca"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Origen"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Estat"), { target: { value: "needs_review" } });
    expect(within(list()).getByText("Solució d'enregistrament de trucades - requeriments tècnics")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Estat"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Obligació"), { target: { value: "Condicional" } });
    expect(within(list()).getAllByRole("listitem")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Obligació"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Categoria"), { target: { value: "economic_solvency" } });
    expect(within(list()).getByText("Volum anual de negocis mínim")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Cerca"), { target: { value: "no existeix" } });
    expect(screen.getByText("Cap requisit no coincideix amb aquests filtres.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Treu els filtres" }));
    expect(within(list()).getAllByRole("listitem")).toHaveLength(8);
  });

  it("origen i estat són dues insígnies separades i neutres", async () => {
    respondWith(palamos());
    await renderPage();

    const m15 = screen.getByRole("button", { name: /Mínim 64 trucades simultànies/ });
    expect(within(m15).getByText("Afegit manualment")).toBeTruthy();
    expect(within(m15).getByText("Pendent de revisar")).toBeTruthy();
    const provenance = within(m15).getByText("Afegit manualment").closest("[data-level]") as HTMLElement;
    const status = within(m15).getByText("Pendent de revisar").closest("[data-level]") as HTMLElement;
    expect(provenance).not.toBe(status);
    // Manual no vol dir dubtós: el mateix to neutre que l'extret.
    expect(provenance.dataset.level).toBe("neutral");
    const automated = screen.getByRole("button", { name: /^Troncal SIP - numeració múltiple DDI\/DID/ });
    expect((within(automated).getByText("Extret automàticament").closest("[data-level]") as HTMLElement).dataset.level).toBe("neutral");
    // No depèn del color: cada insígnia porta el seu rètol per als lectors de pantalla.
    expect(within(m15).getByText("Origen:")).toBeTruthy();
    expect(within(m15).getByText("Estat:")).toBeTruthy();
  });

  it("M15: 64 trucades, afegit manualment, pendent de revisar, PPT p. 23", async () => {
    respondWith(palamos());
    await renderPage();

    const m15 = screen.getByRole("button", { name: /Mínim 64 trucades simultànies/ });
    expect(within(m15).getByText("≥ 64 trucades")).toBeTruthy();
    expect(within(m15).getByText("PPT · p. 23")).toBeTruthy();

    m15.focus();
    fireEvent.click(m15);
    const detail = screen.getByRole("dialog", { name: "Mínim 64 trucades simultànies" });
    expect(within(detail).getByText("≥ 64 trucades")).toBeTruthy();
    // El valor d'un requisit manual el diu «Afegit manualment», com l'origen.
    expect(within(detail).getAllByText("Afegit manualment").length).toBeGreaterThanOrEqual(2);
    expect(within(detail).queryByText(/Afirmat pel revisor/)).toBeNull();
    expect(within(detail).getByText("Pendent de revisar")).toBeTruthy();
    expect(within(detail).getByText("p. 23")).toBeTruthy();
    expect(within(detail).getByText("Citat manualment")).toBeTruthy();
    expect(within(detail).getByText("«mínim de 64 trucades simultànies»")).toBeTruthy();
    expect(within(detail).getByText("Requisit tècnic")).toBeTruthy();
    expect(within(detail).getByText("Obligatori")).toBeTruthy();
    // Al detall el rètol ja és visible: el lector de pantalla no el repeteix.
    expect(within(detail).queryByText("Estat:")).toBeNull();
    expect(within(detail).queryByText("Origen:")).toBeNull();
  });

  it("M06 i M21: parcial i relacionat, sense semblar duplicats", async () => {
    respondWith(palamos());
    await renderPage();

    fireEvent.click(screen.getByRole("button", { name: /Solució cloud d'alta disponibilitat/ }));
    let detail = screen.getByRole("dialog");
    expect(within(detail).getByText("Cobert parcialment per:")).toBeTruthy();
    fireEvent.click(within(detail).getByRole("button", { name: "Plataforma de comunicacions de veu fixa en alta disponibilitat al núvol" }));
    detail = screen.getByRole("dialog", { name: "Plataforma de comunicacions de veu fixa en alta disponibilitat al núvol" });
    expect(within(detail).getByText("Extret automàticament")).toBeTruthy();
    expect(within(detail).getByText("Cobreix parcialment:")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: /Accés directe a extensió \(DDI\)/ }));
    detail = screen.getByRole("dialog", { name: "Accés directe a extensió (DDI)" });
    expect(within(detail).getByText("Relacionat amb:")).toBeTruthy();
    // La nota de l'importador sense l'identificador d'annex: aquest queda a la traçabilitat.
    expect(within(detail).getByText("Relacionats, no equivalents")).toBeTruthy();
    expect(within(detail).queryByText("Relacionats, no equivalents (annex M21).")).toBeNull();
    expect(within(detail).getByText("Traçabilitat de la importació")).toBeTruthy();
    expect(within(detail).queryByText(/Duplicat/)).toBeNull();
    // Cada un és a la llista pel seu compte: res plegat.
    const list = screen.getByRole("list", { name: "Requisits" });
    expect(within(list).getByRole("button", { name: /^Troncal SIP - numeració múltiple DDI\/DID/ })).toBeTruthy();
    expect(within(list).getByRole("button", { name: /Accés directe a extensió \(DDI\)/ })).toBeTruthy();
  });

  it("M09: l'evidència original i la cita manual de p. 22", async () => {
    respondWith(palamos());
    await renderPage();

    const m09 = screen.getByRole("button", { name: /Connectivitat a nivell 2/ });
    expect(within(m09).getByText("PPT · p. 22, 31")).toBeTruthy();
    expect(within(m09).getByText("Extret automàticament")).toBeTruthy();
    fireEvent.click(m09);
    const detail = screen.getByRole("dialog");
    const evidence = within(detail).getByRole("heading", { name: "Evidència" }).parentElement as HTMLElement;
    expect(within(evidence).getAllByRole("listitem")).toHaveLength(2);
    expect(within(evidence).getByText("p. 31")).toBeTruthy();
    expect(within(evidence).getByText("p. 22")).toBeTruthy();
    expect(within(evidence).getByText("§ 8.1.1.4")).toBeTruthy();
    // Els salts de línia del PDF no trenquen la cita.
    expect(within(evidence).getByText("«connectivitat a nivell 2 amb el datacenter»")).toBeTruthy();
    expect(within(evidence).getByText("Citat manualment")).toBeTruthy();
    expect(within(evidence).getByText("Cita sense valors estructurats automàticament")).toBeTruthy();
    expect(within(detail).getByText("Versió de revisió 2")).toBeTruthy();
  });

  it("el detall s'obre amb el teclat i Escape el tanca", async () => {
    respondWith(palamos());
    await renderPage();

    const row = screen.getByRole("button", { name: /Mínim 64 trucades simultànies/ });
    expect(row.tagName).toBe("BUTTON");
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.click(row); // un <button> activat amb Enter dispara «click»
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("no ensenya cap codi intern", async () => {
    respondWith(palamos());
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Veure estructura completa" }));
    await flush();
    for (const id of ["m09", "m21"]) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(id === "m09" ? "Connectivitat" : "Accés directe") }));
      const text = document.body.textContent ?? "";
      for (const code of FORBIDDEN) expect(text).not.toContain(code);
      fireEvent.keyDown(document, { key: "Escape" });
    }
  });

  it("mentre carrega, un esquelet i cap xifra provisional", async () => {
    get.mockImplementation(() => new Promise(() => {}));
    render(<TenderReviewPage />);
    await flush();
    expect(document.querySelector("[aria-busy='true']")).toBeTruthy();
    expect(screen.getByText("Carregant la revisió…")).toBeTruthy();
    expect(screen.queryByText("257")).toBeNull();
  });

  it("si la revisió no s'ha iniciat, ho diu i no escriu res", async () => {
    respondWith(palamos({ [`${REVIEW}/overview`]: { ...reviewOverview, review_started: false, total_requirements: 0 } }));
    await renderPage();

    expect(screen.getByText("La revisió encara no s'ha iniciat")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure els requisits de la fitxa" })).toBeTruthy();
    expect(requested()).not.toContain(`${REVIEW}/requirements`);
    expect(write).not.toHaveBeenCalled();
  });

  it("un error de l'API es pot tornar a provar", async () => {
    respondWith(palamos({ [`${REVIEW}/overview`]: Object.assign(new Error("Error 500"), { status: 500 }) }));
    await renderPage();

    expect(screen.getByText("No s'ha pogut carregar la revisió")).toBeTruthy();
    respondWith(palamos());
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    await flush();
    await flush();
    expect(screen.getByRole("list", { name: "Criteris que puntuen" })).toBeTruthy();
  });

  it("si només fallen els criteris, la resta es veu igualment", async () => {
    respondWith(palamos({ [`${REVIEW}/award-criteria?view=scoring`]: Object.assign(new Error("Error 500"), { status: 500 }) }));
    await renderPage();

    expect(screen.getByText("No s'han pogut llegir els criteris")).toBeTruthy();
    expect(screen.getByRole("list", { name: "Requisits" })).toBeTruthy();
  });

  it("criteris encara sense paper: ho explica en lloc de mostrar 0 punts com a resultat", async () => {
    respondWith(
      palamos({
        [`${REVIEW}/award-criteria?view=scoring`]: {
          view: "scoring",
          criteria: [],
          scored_count: 0,
          total_points: 0,
          checks: [{ code: "role_unassigned", label: "Paper per assignar", item_ids: ["x"], details: {} }],
        },
      }),
    );
    await renderPage();

    expect(screen.getByText(/Encara no s'ha decidit quins criteris puntuen/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Veure estructura completa" })).toBeNull();
  });
});
