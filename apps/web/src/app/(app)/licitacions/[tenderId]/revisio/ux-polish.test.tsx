import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { CanonicalRelation, CanonicalRequirement, ReviewHistoryEntry } from "@/lib/review";
import { awardAll, awardScoring, reviewOverview, reviewRequirements } from "@/test/review-fixture";

/**
 * Poliment a partir de les proves d'usuari (Palamós): el perquè de «Cal revisar»,
 * corregit però no verificat, d'on surt cada valor, la cita al costat de la
 * correcció, relacions agrupades, historial sense codis interns i criteris.
 * Tot amb dades SINTÈTIQUES amb forma de Palamós; cap desa res.
 */

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) },
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
const PPT = "PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf";
const LATENCY_QUOTE = "Latència màx. entre dos punts en serveis de dades <70 ms Greu";

function base(overrides: Partial<CanonicalRequirement> & Pick<CanonicalRequirement, "canonical_id" | "title">): CanonicalRequirement {
  const item = {
    description: null,
    category: "technical_requirement",
    category_label: "Requisit tècnic",
    obligation_label: "Obligatori",
    provenance: "automated",
    provenance_label: "Extret automàticament",
    verification_status: "needs_review",
    verification_label: "Cal revisar",
    needs_review: true,
    values: [],
    evidence_label: "Cita sense valors estructurats automàticament",
    evidence: [],
    documents: [PPT],
    pages: [],
    sections: [],
    source_refs: [],
    review_revision: 1,
    visible_relations: [],
    folded_relations: [],
    review_reasons: [],
    ...overrides,
  };
  return {
    ...item,
    editable: {
      title: item.title,
      description: item.description,
      category: item.category,
      obligation: "mandatory",
      modality: "mandatory",
      values: item.values.map((value) => ({
        key: value.key, operator: value.operator, value: value.value, unit_label: value.unit_label,
        source: value.source ?? "document",
      })),
    },
  } as CanonicalRequirement;
}

function relation(id: string, direction: "outgoing" | "incoming", label: string, other: string, title: string, note: string | null): CanonicalRelation {
  return {
    relation_id: id, direction, relation_type: "partial_match_of", relation_label: label,
    other_item_id: other, other_item_title: title, note, foldable: false, created_at: "2026-09-27T20:00:00Z",
  };
}

const LATENCY = base({
  canonical_id: "u-latency",
  title: "Latència màxima en serveis de dades",
  provenance: "automated_corrected",
  provenance_label: "Extret i corregit",
  review_revision: 6,
  values: [{ key: "reviewer_value_1", operator: "<", value: 70, unit_label: "ms", source: "reviewer",
             source_label: "Corregit pel revisor", status_label: "Corregit pel revisor", context: null }],
  evidence: [{ document: PPT, page: 43, section: null, quote: LATENCY_QUOTE,
               status_label: "Cita sense valors estructurats automàticament" }],
  pages: [43],
  review_reasons: [{ code: "corrected_not_verified",
                     label: "El contingut s'ha corregit, però el requisit encara no s'ha verificat." }],
});

const FAULTS = base({
  canonical_id: "u-faults",
  title: "Classificació d'avaries per nivell d'afectació",
  obligation_label: "Informatiu",
  values: [
    { key: "avaria_alt_percent", operator: null, value: 50, unit_label: "percent", source: "document",
      source_label: "Extret del document", status_label: "Confirmat al document",
      context: "afectació de més del 50% d’usuaris" },
    { key: "avaria_baix_percent", operator: null, value: 10, unit_label: "percent", source: "document",
      source_label: "Extret del document", status_label: "Confirmat al document",
      context: "afectació de menys del 10% d’usuaris" },
  ],
  review_reasons: [{ code: "value_unverified", label: "Algun valor extret no s'ha pogut comprovar amb la cita." }],
});

const PLATFORM = base({
  canonical_id: "u-platform",
  title: "Plataforma de veu al núvol (prova UX)",
  visible_relations: ["M06", "M18", "M39"].map((annex, index) =>
    relation(`rel-${annex}`, "incoming", "Cobreix parcialment", `u-m${index}`, `Requisit manual ${annex}`,
             `Coincidencia parcial amb l'element automatic (annex ${annex}).`)),
  review_reasons: [{ code: "value_unverified", label: "Algun valor extret no s'ha pogut comprovar amb la cita." }],
});

const DDI = base({
  canonical_id: "u-ddi",
  title: "Accés DDI (prova UX)",
  provenance: "manual",
  provenance_label: "Afegit manualment",
  verification_status: "unreviewed",
  verification_label: "Pendent de revisar",
  needs_review: false,
  visible_relations: [
    { ...relation("rel-M21", "outgoing", "Relacionat amb", "u-platform", "Troncal SIP", "Relacionats, no equivalents (annex M21).") ,
      relation_type: "related_to" },
  ],
});

const IMPORTED_HISTORY: ReviewHistoryEntry[] = [
  {
    revision_no: 1, action: "create", action_label: "Creat", status_after: "unreviewed", status_label: "Pendent de revisar",
    note: "Només esmentada dins la fila de dimensionament p32 (needs_review).", actor_id: "import", created_at: "2026-09-27T20:00:00Z",
    changes: { import_batch_id: "b1", package: "palamos_final_review", manual_id: "M21" },
  },
  {
    revision_no: 2, action: "link_source", action_label: "Evidència afegida", status_after: "unreviewed",
    status_label: "Pendent de revisar", note: "També ho diu explícitament el PPT p22 §8.1.1.4 (annex M09).", actor_id: "import",
    created_at: "2026-09-27T20:01:00Z", changes: { import_batch_id: "b1", package: "palamos_final_review" },
  },
  {
    revision_no: 3, action: "note", action_label: "Nota", status_after: "unreviewed", status_label: "Pendent de revisar",
    note: "Revisat amb el client: needs_review no aplica.", actor_id: "u1", created_at: "2026-09-28T10:00:00Z", changes: {},
  },
];

const ITEMS = [...reviewRequirements, LATENCY, FAULTS, PLATFORM, DDI];

function respond() {
  const routes: Record<string, unknown> = {
    "/tenders/t1": { title: "Palamós" },
    [`${REVIEW}/overview`]: reviewOverview,
    [`${REVIEW}/requirements`]: ITEMS,
    [`${REVIEW}/award-criteria?view=scoring`]: awardScoring,
    [`${REVIEW}/award-criteria?view=all`]: awardAll,
    [`${REVIEW}/items/u-ddi/history`]: IMPORTED_HISTORY,
  };
  get.mockImplementation((path: string) => {
    if (path in routes) return Promise.resolve(routes[path]);
    if (path.endsWith("/history")) return Promise.resolve([]);
    return Promise.reject(new ApiError(404, "not_found", "No trobat"));
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

async function open(title: RegExp) {
  render(<TenderReviewPage />);
  await flush();
  await flush();
  fireEvent.click(within(screen.getByRole("list", { name: "Requisits" })).getByRole("button", { name: title }));
  await flush();
  return screen.getByRole("dialog");
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  post.mockImplementation(() => { throw new Error("cap acció es desa en aquests tests"); });
  respond();
});

describe("Aspecte SaaS clar a la revisió", () => {
  it("les xifres del resum i la secció de revisió fan servir el radi i la superfície de targeta", async () => {
    const dialog = await open(/Latència màxima/);
    const stat = screen.getByText("Requisits", { selector: "dt" }).closest("div")!;
    expect(stat.className).toMatch(/rounded-card/);
    expect(stat.className).toMatch(/bg-surface/);
    const section = within(dialog).getByRole("heading", { name: "Revisió" }).closest("section")!;
    expect(section.className).toMatch(/rounded-card/);
  });
});

describe("Per què cal revisar", () => {
  it("corregit però no verificat: es diu en una frase, el valor és del revisor i la cita no diu «sense valors» al plec", async () => {
    const dialog = await open(/Latència màxima/);
    // El mateix encapçalament que qualsevol altre motiu.
    const reasons = within(dialog).getByRole("group", { name: "Per què cal revisar?" });
    expect(within(reasons).getByText("Per què cal revisar?")).toBeTruthy();
    expect(within(reasons).getByText("El contingut s'ha corregit, però el requisit encara no s'ha verificat.")).toBeTruthy();
    expect(within(dialog).getByText("< 70 ms")).toBeTruthy();
    expect(within(dialog).getAllByText("Corregit pel revisor").length).toBeGreaterThan(0);
    expect(within(dialog).getByText("Cita sense valors estructurats automàticament")).toBeTruthy();
    expect(within(dialog).queryByText(/Al document, sense valors/)).toBeNull();
    expect(within(dialog).queryByText(/Afirmat pel revisor/)).toBeNull();
  });

  it("un «Cal revisar» amb motius deterministes els llista", async () => {
    const dialog = await open(/Classificació d'avaries/);
    const reasons = within(dialog).getByRole("group", { name: "Per què cal revisar?" });
    expect(within(reasons).getByText("Per què cal revisar?")).toBeTruthy();
    expect(within(reasons).getByText("Algun valor extret no s'ha pogut comprovar amb la cita.")).toBeTruthy();
  });

  it("els valors extrets porten el fragment literal del plec, sense etiquetes inventades", async () => {
    const dialog = await open(/Classificació d'avaries/);
    expect(within(dialog).getByText("«afectació de més del 50% d’usuaris»")).toBeTruthy();
    expect(within(dialog).getByText("«afectació de menys del 10% d’usuaris»")).toBeTruthy();
    expect(within(dialog).queryByText(/Nivell alt|Nivell mitjà|Nivell baix/)).toBeNull();
  });

  it("la versió de revisió és metadada secundària, no un fet al costat de Categoria i Origen", async () => {
    const dialog = await open(/Latència màxima/);
    const facts = [...dialog.querySelectorAll("dt")].map((dt) => dt.textContent);
    expect(facts).not.toContain("Revisió");
    const version = within(dialog).getByText(/^Versió de revisió \d+$/);
    expect(version.tagName).toBe("P");
    expect(version.className).toMatch(/text-muted/);
    expect(version.closest("dl")).toBeNull();
  });

  it("confirmar diu què confirma i l'aplicació es mostra a part de l'obligació", async () => {
    const dialog = await open(/Latència màxima/);
    expect(within(dialog).getByRole("button", { name: "Confirmar requisit" })).toBeTruthy();
    const application = within(dialog).getByText("Aplicació").closest("div")!;
    expect(within(application).getByText("Sempre")).toBeTruthy();
    const obligation = within(dialog).getByText("Obligació").closest("div")!;
    expect(within(obligation).getByText("Obligatori")).toBeTruthy();
  });
});

describe("Editor de correccions compacte", () => {
  it("mentre es corregeix, la cita és al costat dels valors i el detall no es repeteix sencer", async () => {
    const dialog = await open(/Latència màxima/);
    const historyCalls = () => get.mock.calls.filter(([path]) => String(path).endsWith("/u-latency/history")).length;
    const before = historyCalls();
    fireEvent.click(within(dialog).getByRole("button", { name: "Corregir" }));
    await flush();

    const editor = within(dialog).getByRole("group", { name: "Corregir el requisit" });
    const reference = within(editor).getByRole("region", { name: "Evidència de referència" });
    expect(within(reference).getByText(/p\. 43/)).toBeTruthy();
    expect(within(reference).getByText(/<70 ms/)).toBeTruthy();
    expect(within(editor).getByRole("group", { name: "Valor 1" })).toBeTruthy();
    // El detall normal (valors, historial...) queda plegat, però accessible.
    const detail = dialog.querySelector("[id^='detall-u-latency']") as HTMLElement;
    expect(detail.hidden).toBe(true);
    const toggle = within(dialog).getByRole("button", { name: "Consulta el detall actual" });
    fireEvent.click(toggle);
    expect(detail.hidden).toBe(false);
    // Amagar-lo no el desmunta: l'historial no es torna a llegir.
    expect(historyCalls()).toBe(before);
    expect(within(editor).getByText(/corregit pel revisor/)).toBeTruthy();
  });
});

describe("Relacions i historial sense vocabulari intern", () => {
  it("diverses coincidències parcials s'agrupen i l'identificador d'annex va a la traçabilitat", async () => {
    const dialog = await open(/Plataforma de veu al núvol \(prova UX\)/);
    expect(within(dialog).getByText("Cobreix parcialment (3)")).toBeTruthy();
    for (const annex of ["M06", "M18", "M39"]) {
      expect(within(dialog).getByRole("button", { name: `Requisit manual ${annex}` })).toBeTruthy();
      for (const node of within(dialog).queryAllByText(new RegExp(`annex ${annex}`))) {
        expect(node.closest("details")).not.toBeNull();
      }
    }
    expect(within(dialog).queryByText(/Coincidencia parcial amb l'element automatic/)).toBeNull();
  });

  it("l'historial importat diu «cal revisar» i no «needs_review»; una nota humana no es toca", async () => {
    const dialog = await open(/Accés DDI \(prova UX\)/);
    await flush();
    const history = within(dialog).getByRole("heading", { name: "Historial de revisió" }).closest("section")!;
    expect(within(history).getByText("Només esmentada dins la fila de dimensionament p32 (cal revisar).")).toBeTruthy();
    expect(within(history).getByText("També ho diu explícitament el PPT p22 §8.1.1.4.")).toBeTruthy();
    expect(within(history).getByText("Importat de la revisió manual · traçabilitat: annex M09")).toBeTruthy();
    // La nota escrita per una persona es mostra tal com és, encara que porti un codi.
    expect(within(history).getByText("Revisat amb el client: needs_review no aplica.")).toBeTruthy();
    expect(within(dialog).getByText("Relacionats, no equivalents")).toBeTruthy();
  });

  it("un requisit manual diu «Afegit manualment» també al valor", async () => {
    const dialog = await open(/Mínim 64 trucades simultànies/);
    expect(within(dialog).getByText("≥ 64 trucades")).toBeTruthy();
    expect(within(dialog).getAllByText("Afegit manualment").length).toBeGreaterThanOrEqual(2);
    expect(within(dialog).getByText("Citat manualment")).toBeTruthy();
  });
});

describe("Criteris d'adjudicació", () => {
  it("la vista reduïda diu «criteris que puntuen» i el total no canvia", async () => {
    render(<TenderReviewPage />);
    await flush();
    await flush();
    expect(screen.getByText("100 punts totals · 9 criteris que puntuen")).toBeTruthy();
    expect(screen.getByText("80 punts")).toBeTruthy();
  });

  it("l'estructura completa no fa semblar que les repeticions sumin i explica el total del bloc", async () => {
    render(<TenderReviewPage />);
    await flush();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Veure estructura completa" }));
    await flush();
    await flush();
    expect(screen.getByRole("button", { name: "Mostra només els 9 criteris que sumen punts" })).toBeTruthy();
    const repeated = screen.getByRole("heading", { name: "Repeticions en altres documents" }).closest("section")!;
    expect(within(repeated).queryByText("Puntua")).toBeNull();
    expect(within(repeated).getAllByText("Repetició · no suma de nou").length).toBeGreaterThan(0);
    const totals = screen.getByRole("heading", { name: "Totals de bloc" }).closest("section")!;
    expect(within(totals).getByText("Total informatiu: aquests 20 punts ja provenen dels criteris del bloc.")).toBeTruthy();
  });

  it("els avisos: fórmula pendent de verificar i cada repetició exclosa en una fila", async () => {
    render(<TenderReviewPage />);
    await flush();
    await flush();
    const warnings = screen.getByRole("heading", { name: "Per tenir en compte" }).closest("section")!;
    expect(within(warnings).getByText("Fórmula pendent de verificar")).toBeTruthy();
    const excluded = within(warnings).getByText(/Reiteració exclosa del total/).closest("div")!;
    expect(within(excluded).getAllByRole("listitem").length).toBe(2);
  });
});
