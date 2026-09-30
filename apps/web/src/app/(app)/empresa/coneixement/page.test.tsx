import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  emptySnapshotFixture,
  legacyCertificationFixture,
  observationsFixture,
  recordsFixture,
  snapshotFixture,
  vocabularyFixture,
} from "@/test/knowledge-fixture";
import type { Observation } from "@/lib/knowledge";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const remove = vi.fn();
const replace = vi.fn();
let role = "owner";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      get: (...args: unknown[]) => get(...args),
      post: (...args: unknown[]) => post(...args),
      patch: (...args: unknown[]) => patch(...args),
      delete: (...args: unknown[]) => remove(...args),
    },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", membership: { role, company: { id: "c1", legal_name: "Empresa Demo SL", trade_name: null } } }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/empresa/coneixement",
  useSearchParams: () => new URLSearchParams(""),
}));
// El panell d'evidències té les seves proves: aquí només cal saber que s'obre
// sobre l'entitat correcta i que avisa quan l'evidència canvia.
vi.mock("@/components/EvidencePanel", () => ({
  EvidencePanel: ({ entityType, entityId, onChange, readOnly }: { entityType: string; entityId: string; onChange?: () => void; readOnly?: boolean }) => (
    <div data-testid="evidence" data-entity={`${entityType}:${entityId}`} data-readonly={readOnly ? "true" : "false"}>
      <button type="button" onClick={() => onChange?.()}>
        simula evidència nova
      </button>
    </div>
  ),
}));

import { ApiError } from "@/lib/api";

import CompanyKnowledgePage from "./page";

const BASE = "/companies/c1";
const RAW_CODES = /current_verified|current_declared|scope_unknown|pending_verification|historic_candidate|accreditation_pending|promoted_observation/;

function respond(overrides: Record<string, unknown> = {}) {
  const map: Record<string, unknown> = {
    [`${BASE}/knowledge/snapshot`]: snapshotFixture(),
    [`${BASE}/knowledge/observations?status=candidate`]: observationsFixture,
    [`${BASE}/knowledge/records`]: recordsFixture(),
    [`${BASE}/certifications`]: [legacyCertificationFixture],
    [`${BASE}/services`]: [],
    [`${BASE}/knowledge/vocabulary`]: vocabularyFixture,
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    const value = map[path];
    return value instanceof Error ? Promise.reject(value) : Promise.resolve(value);
  });
}

function respondEmpty() {
  respond({
    [`${BASE}/knowledge/snapshot`]: emptySnapshotFixture(),
    [`${BASE}/knowledge/observations?status=candidate`]: [],
    [`${BASE}/knowledge/records`]: { operating_facts: [], solutions: [], solution_capabilities: [], products: [], solution_products: [], partners: [], partner_roles: [] },
    [`${BASE}/certifications`]: [],
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

async function renderPage() {
  const view = render(<CompanyKnowledgePage />);
  await screen.findByRole("tablist");
  await flush();
  return view;
}

function record(container: HTMLElement, id: string): HTMLElement {
  const row = container.querySelector<HTMLElement>(`[data-record="${id}"]`);
  if (!row) throw new Error(`No hi ha la fila ${id}`);
  return row;
}

function tile(container: HTMLElement, key: string): string {
  return container.querySelector(`[data-tile="${key}"] dd`)?.textContent ?? "";
}

function snapshotCalls(): number {
  return get.mock.calls.filter(([path]) => path === `${BASE}/knowledge/snapshot`).length;
}

describe("Coneixement de l'empresa", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    patch.mockReset();
    remove.mockReset();
    replace.mockReset();
    role = "owner";
    respond();
  });

  it("resumeix què se sap i obre a «Pendents de validar» quan hi ha feina", async () => {
    const { container } = await renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Coneixement de l'empresa" })).toBeTruthy();
    expect(["verified", "declared", "pending", "historic", "unknown"].map((key) => tile(container, key))).toEqual(["1", "2", "2", "4", "6"]);
    expect(screen.getByRole("tab", { name: /Pendents de validar/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("heading", { name: "Dades actuals per acreditar" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Detectat a l'històric" })).toBeTruthy();
    // Els suggeriments van per categoria, i al final els que només es poden descartar.
    expect([...container.querySelectorAll("[data-candidates]")].map((group) => group.querySelector("p")?.textContent)).toEqual([
      "Fets operatius · 1",
      "Solucions · 1",
      "Només es poden descartar · 2",
    ]);
    // Cap codi intern a la pantalla: tot són paraules.
    expect(container.textContent).not.toMatch(RAW_CODES);
  });

  it("una fila antiga marcada «verificada» sense evidència NO surt com a Verificat", async () => {
    const { container } = await renderPage();
    // La fila de la base diu `verified`; la fotografia la dona per declarada.
    expect(legacyCertificationFixture.verification_status).toBe("verified");
    const row = record(container, "cert-ens");
    expect(within(row).getByText("ENS · Mitjà")).toBeTruthy();
    expect(within(row).getByText("Declarat")).toBeTruthy();
    expect(within(row).queryByText("Verificat")).toBeNull();
    expect(row.querySelector("[data-state]")?.getAttribute("data-state")).toBe("declared");
    expect(row.textContent).toContain("Falta evidència");
    expect(row.textContent).toContain("Abast desconegut");

    // També a «Coneixement actual».
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    const current = record(container, "cert-ens");
    expect(within(current).queryByText("Verificat")).toBeNull();
    expect(within(current).getByText("Declarat")).toBeTruthy();
  });

  it("pendents: diu què hi falta a cada dada i n'ofereix la sortida", async () => {
    const { container } = await renderPage();
    const ens = record(container, "cert-ens");
    expect(ens.textContent).toContain("Encara no hi ha cap document que ho acrediti.");
    expect(ens.textContent).toContain("No se sap quines línies de servei cobreix.");
    expect(within(ens).getByRole("button", { name: "Afegeix evidència" })).toBeTruthy();
    expect(within(ens).getByRole("button", { name: "Edita l'abast" })).toBeTruthy();
    expect(within(ens).getByRole("button", { name: "Verifica" })).toBeTruthy();

    // El partner ja té evidència: només falta donar-la per bona.
    const partner = record(container, "partner-1");
    expect(partner.textContent).toContain("Pendent de verificar");
    expect(partner.textContent).not.toContain("Falta evidència");
    // Els papers d'un partner no surten com a pendents a part.
    expect(container.querySelector('[data-record="role-1"]')).toBeNull();
  });

  it("verificar sense evidència: explica el 422 i obre el panell d'evidència", async () => {
    post.mockRejectedValue(new ApiError(422, "evidence_required", "Nomes es pot marcar com a verificat amb evidencia enllacada."));
    const { container } = await renderPage();
    const ens = record(container, "cert-ens");
    expect(within(ens).queryByTestId("evidence")).toBeNull();

    fireEvent.click(within(ens).getByRole("button", { name: "Verifica" }));
    await flush();

    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/certification/cert-ens/verify`, {});
    expect(within(ens).getByRole("alert").textContent).toContain("Per verificar cal evidència enllaçada");
    expect(within(ens).getByTestId("evidence").getAttribute("data-entity")).toBe("company_certification:cert-ens");
    // No ha canviat res: la dada continua declarada.
    expect(within(ens).getByText("Declarat")).toBeTruthy();
  });

  it("afegir evidència rellegeix la fotografia, i verificar-la amb evidència fa el POST", async () => {
    post.mockResolvedValue({ kind: "partner", record_id: "partner-1", verification_status: "verified" });
    const { container } = await renderPage();
    const partner = record(container, "partner-1");

    fireEvent.click(within(partner).getByRole("button", { name: "Evidència" }));
    expect(within(partner).getByTestId("evidence").getAttribute("data-entity")).toBe("company_partner:partner-1");
    const before = snapshotCalls();
    fireEvent.click(within(partner).getByRole("button", { name: "simula evidència nova" }));
    await flush();
    expect(snapshotCalls()).toBe(before + 1);

    fireEvent.click(within(partner).getByRole("button", { name: "Verifica" }));
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/partner/partner-1/verify`, {});
    expect(snapshotCalls()).toBe(before + 2);
  });

  it("edita l'abast d'una certificació: el text és una nota i es desen línies del vocabulari", async () => {
    patch.mockResolvedValue({});
    const { container } = await renderPage();
    fireEvent.click(within(record(container, "cert-ens")).getByRole("button", { name: "Edita l'abast" }));

    const dialog = screen.getByRole("dialog", { name: "Abast de ENS · Mitjà" });
    expect(dialog.textContent).toContain("Cobreix la centraleta i la xarxa dedicada");
    expect(dialog.textContent).toContain("no es fa servir per deduir què cobreix el certificat");
    // El text parla de la centraleta, però no hi ha cap línia marcada: no s'infereix res.
    expect(within(dialog).getAllByRole("checkbox").every((box) => !(box as HTMLInputElement).checked)).toBe(true);
    expect(dialog.textContent).toContain("l'abast queda com a desconegut");

    fireEvent.click(within(dialog).getByLabelText("Centraleta i veu IP"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Desa l'abast" }));
    await flush();
    expect(patch).toHaveBeenCalledWith(`${BASE}/certifications/cert-ens`, { scope_service_lines: ["voice_pbx"] });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sense abast estructurat mostra «Abast pendent d'estructurar» i el text només com a nota", async () => {
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    const row = record(container, "cert-ens");
    expect(within(row).getByText("Abast pendent d'estructurar")).toBeTruthy();
    expect(row.textContent).toContain("Nota d'abast: «Cobreix la centraleta i la xarxa dedicada»");
    expect(row.textContent).toContain("Sense data de caducitat");
  });

  it("confirmar un candidat el promou a declarat, mai a verificat, i ho diu abans", async () => {
    post.mockResolvedValue({});
    await renderPage();
    const before = snapshotCalls();
    fireEvent.click(screen.getByRole("button", { name: "Revisa i confirma: Suport 24x7" }));

    const dialog = screen.getByRole("dialog", { name: "Revisa i confirma «Suport 24x7»" });
    expect(dialog.textContent).toContain("Declaraven suport 24x7");
    const saved = within(dialog).getByRole("group", { name: "Si ho confirmes, Tuvi guardarà com a dada actual" });
    expect(saved.querySelector("[data-proposal]")?.textContent).toBe("Suport 24x7: SÍ");
    expect(saved.querySelector("[data-proposal]")?.getAttribute("data-proposal")).toBe("positive");
    // En positiu no hi ha res a advertir.
    expect(within(dialog).queryByRole("note")).toBeNull();
    const after = within(dialog).getByRole("list", { name: "Com quedarà" });
    expect(within(after).getAllByRole("listitem").map((item) => item.querySelector("strong")?.textContent)).toEqual([
      "Declarat",
      "No verificat",
      "Sense evidència",
    ]);
    // Aportar evidència no verifica: són dos passos, i es diu així.
    expect(after.textContent).toContain("Per verificar aquesta dada caldrà aportar evidència i verificar-la.");
    expect(dialog.textContent).not.toMatch(/fins que s'hi enllaci evidència|Pendent d'acreditar/);
    // Encara no s'ha enviat res: obrir el diàleg només és revisar.
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirma i desa" }));
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/observations/o-24x7/promote`, { note: null });
    expect(snapshotCalls()).toBe(before + 1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  describe("una proposta de l'històric es mostra tal com es desarà", () => {
    const ftth: Observation = {
      id: "o-ftth",
      subject: "operating_fact",
      subject_key: "own_ftth_infrastructure",
      // El text de l'històric és ambigu expressament: la pantalla no l'ha de fer servir per dir què es desarà.
      statement: "Xarxa de fibra pròpia: «ho vam estudiar»",
      proposal: { key: "own_ftth_infrastructure", value: false },
      observed_on: "2025-05-23",
      source_ref: "Licitació de mostra",
      status: "candidate",
    };
    const product: Observation = {
      id: "o-prod",
      subject: "product",
      subject_key: "Marca Demo T1",
      statement: "Producte que apareix als càlculs: Marca Demo T1",
      proposal: { manufacturer: "Marca Demo", model: "T1", service_line_code: "voice_pbx" },
      observed_on: null,
      source_ref: null,
      status: "candidate",
    };
    const partner: Observation = {
      id: "o-partner",
      subject: "partner",
      subject_key: "Operador Demo",
      statement: "Operador majorista a l'històric: Operador Demo",
      proposal: { name: "Operador Demo", roles: [{ role: "wholesale_operator", service_line_code: "access_ftth" }, { role: "subcontractor", territory: "Girona" }] },
      observed_on: null,
      source_ref: null,
      status: "candidate",
    };

    async function openHistoric() {
      respond({ [`${BASE}/knowledge/observations?status=candidate`]: [...observationsFixture, ftth, product, partner] });
      const view = await renderPage();
      fireEvent.click(screen.getByRole("tab", { name: /Històric detectat/ }));
      return view;
    }

    function row(container: HTMLElement, id: string): HTMLElement {
      const found = container.querySelector<HTMLElement>(`[data-observation="${id}"]`);
      if (!found) throw new Error(`No hi ha el candidat ${id}`);
      return found;
    }

    function review(name: string): HTMLElement {
      fireEvent.click(screen.getByRole("button", { name: `Revisa i confirma: ${name}` }));
      return screen.getByRole("dialog", { name: `Revisa i confirma «${name}»` });
    }

    const saved = (dialog: HTMLElement) => within(dialog).getByRole("group", { name: "Si ho confirmes, Tuvi guardarà com a dada actual" });
    const fields = (dialog: HTMLElement) =>
      [...saved(dialog).querySelectorAll("dl > div")].map((item) => [item.querySelector("dt")?.textContent, item.querySelector("dd")?.textContent]);

    it("una proposta en negatiu no es pot llegir com una confirmació en positiu", async () => {
      post.mockResolvedValue({});
      const { container } = await openHistoric();
      const candidate = row(container, "o-ftth");

      // A la fila: què indica l'històric, dit en negatiu, i cap botó que convidi a «confirmar» sense mirar.
      expect(candidate.querySelector("[data-proposed]")?.textContent).toBe("L'històric indica: No disposa d'infraestructura FTTH pròpia");
      expect(candidate.querySelector("[data-proposed]")?.getAttribute("data-proposed")).toBe("negative");
      expect(within(candidate).queryByRole("button", { name: /Confirma com a actual/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /Confirma com a actual/ })).toBeNull();

      const dialog = review("Infraestructura FTTH pròpia");
      const proposal = saved(dialog).querySelector("[data-proposal]")!;
      expect(proposal.textContent).toBe("Infraestructura FTTH pròpia: NO");
      expect(proposal.getAttribute("data-proposal")).toBe("negative");
      expect(within(dialog).getByRole("note").textContent).toContain("Es desarà en negatiu");
      // Enlloc del diàleg no hi diu que sí.
      expect(dialog.textContent).not.toMatch(/SÍ|: Sí/);
      expect(dialog.textContent).not.toContain("com a actual");

      // Confirmar envia exactament el mateix que abans: la proposta és la que ja té el servidor.
      fireEvent.click(within(dialog).getByRole("button", { name: "Confirma i desa" }));
      await flush();
      expect(post).toHaveBeenCalledTimes(1);
      expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/observations/o-ftth/promote`, { note: null });
      expect(patch).not.toHaveBeenCalled();
    });

    it("el que es mostra surt de la proposta estructurada, no del text de l'històric", async () => {
      // El mateix text, amb la proposta en positiu: canvia el que es mostra.
      respond({ [`${BASE}/knowledge/observations?status=candidate`]: [{ ...ftth, proposal: { key: "own_ftth_infrastructure", value: true } }] });
      const { container } = await renderPage();
      fireEvent.click(screen.getByRole("tab", { name: /Històric detectat/ }));
      expect(row(container, "o-ftth").querySelector("[data-proposed]")?.textContent).toBe("L'històric indica: Disposa d'infraestructura FTTH pròpia");
      const dialog = review("Infraestructura FTTH pròpia");
      expect(saved(dialog).querySelector("[data-proposal]")?.textContent).toBe("Infraestructura FTTH pròpia: SÍ");
      expect(within(dialog).queryByRole("note")).toBeNull();
      // El text de l'històric hi és, però a part i com a cita.
      expect(dialog.textContent).toContain("Xarxa de fibra pròpia: «ho vam estudiar»");
    });

    it("una solució: el nom i els camps que es crearan", async () => {
      const { container } = await openHistoric();
      expect(row(container, "o-sol").querySelector("[data-proposed]")?.textContent).toBe(
        "L'històric indica: Solució Centraleta Demo · Centraleta i veu IP · fabricant Fabricant Demo",
      );
      const dialog = review("Centraleta Demo");
      expect(saved(dialog).querySelector("[data-proposal]")?.textContent).toBe("Solució: Centraleta Demo");
      expect(fields(dialog)).toEqual([
        ["Línia de servei", "Centraleta i veu IP"],
        ["Fabricant", "Fabricant Demo"],
      ]);
    });

    it("un producte: fabricant, model i línia de servei", async () => {
      post.mockResolvedValue({});
      await openHistoric();
      const dialog = review("Marca Demo T1");
      expect(saved(dialog).querySelector("[data-proposal]")?.textContent).toBe("Producte: Marca Demo T1");
      expect(fields(dialog)).toEqual([
        ["Fabricant", "Marca Demo"],
        ["Model", "T1"],
        ["Línia de servei", "Centraleta i veu IP"],
      ]);
      fireEvent.click(within(dialog).getByRole("button", { name: "Confirma i desa" }));
      await flush();
      expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/observations/o-prod/promote`, { note: null });
    });

    it("un partner: el nom i cada paper que es crearà", async () => {
      const { container } = await openHistoric();
      expect(row(container, "o-partner").querySelector("[data-proposed]")?.textContent).toBe(
        "L'històric indica: Partner Operador Demo: Operador majorista · Accessos FTTH; Subcontractista · Girona",
      );
      const dialog = review("Operador Demo");
      expect(saved(dialog).querySelector("[data-proposal]")?.textContent).toBe("Partner: Operador Demo");
      expect(fields(dialog)).toEqual([
        ["Paper", "Operador majorista · Accessos FTTH"],
        ["Paper", "Subcontractista · Girona"],
      ]);
    });

    it("una certificació que no es tenia es diu en negatiu i continua sense poder-se confirmar", async () => {
      const { container } = await openHistoric();
      const iso = row(container, "o-iso");
      expect(iso.querySelector("[data-proposed]")?.textContent).toBe("L'històric indica: No disposava de la certificació ISO 9001");
      expect(within(iso).queryByRole("button", { name: /Revisa i confirma/ })).toBeNull();
      expect(within(iso).getByRole("button", { name: "Descarta: ISO 9001" })).toBeTruthy();
    });

    it("a «Coneixement actual» també es veu què indica l'històric abans de confirmar", async () => {
      const snapshot = snapshotFixture();
      snapshot.facts.push({
        key: "own_ftth_infrastructure",
        source: "historic",
        accreditation_pending: false,
        claims: [],
        hints: [{ observation_id: "o-ftth", statement: ftth.statement, observed_on: ftth.observed_on, source_ref: ftth.source_ref }],
      });
      respond({ [`${BASE}/knowledge/snapshot`]: snapshot, [`${BASE}/knowledge/observations?status=candidate`]: [...observationsFixture, ftth] });
      const { container } = await renderPage();
      fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
      const facts = container.querySelector("#fets")!;
      expect([...facts.querySelectorAll("[data-proposed]")].map((item) => item.textContent)).toEqual([
        "L'històric indica: Ofereix suport 24x7",
        "L'històric indica: No disposa d'infraestructura FTTH pròpia",
      ]);
      expect(within(facts as HTMLElement).getByRole("button", { name: "Revisa i confirma: Infraestructura FTTH pròpia" })).toBeTruthy();
    });
  });

  it("descartar un candidat demana el motiu", async () => {
    post.mockResolvedValue({});
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Descarta: Centraleta Demo" }));
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Descarta" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "Ja no l'oferim" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/observations/o-sol/dismiss`, { note: "Ja no l'oferim" });
  });

  it("una observació negativa o ja superada per una dada actual només es pot descartar", async () => {
    await renderPage();
    expect(screen.queryByRole("button", { name: "Revisa i confirma: ISO 9001" })).toBeNull();
    expect(screen.getByRole("button", { name: "Descarta: ISO 9001" })).toBeTruthy();
    expect(screen.getByText(/L'històric diu que no es tenia/)).toBeTruthy();
    // ENS ja està declarat: el que en diu l'històric no el pot trepitjar.
    expect(screen.queryByRole("button", { name: "Revisa i confirma: ENS" })).toBeNull();
    expect(screen.getByText(/Ja hi ha una dada actual sobre això/)).toBeTruthy();
  });

  it("si la promoció xoca amb una dada actual, l'error es queda al diàleg", async () => {
    post.mockRejectedValue(new ApiError(409, "knowledge_already_declared", "Ja hi ha coneixement actual sobre support_24x7."));
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Revisa i confirma: Suport 24x7" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirma i desa" }));
    await flush();
    expect(screen.getByRole("dialog").textContent).toContain("Ja hi ha coneixement actual");
  });

  it("coneixement actual: cada categoria amb els seus estats, l'històric i el que no se sap", async () => {
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    expect(replace).toHaveBeenCalledWith("/empresa/coneixement?tab=actual", { scroll: false });
    for (const name of ["Certificacions", "Serveis", "Fets operatius", "Solucions", "Capacitats", "Productes", "Partners"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeTruthy();
    }

    // Verificat, amb el seu detall.
    const tier3 = record(container, "fact-tier3");
    expect(within(tier3).getByText("Centre de dades Tier-3")).toBeTruthy();
    expect(within(tier3).getByText("Verificat")).toBeTruthy();
    expect(tier3.textContent).toContain("Sí · CPD en un centre Tier-3");
    expect(within(tier3).queryByRole("button", { name: "Verifica" })).toBeNull();

    // Només a l'històric: es pot confirmar des de la mateixa categoria.
    const facts = container.querySelector("#fets")!;
    const historic = facts.querySelector<HTMLElement>('[data-gap="historic"]')!;
    expect(historic.textContent).toContain("Suport 24x7");
    expect(historic.textContent).toContain("Històric detectat");
    expect(within(historic).getByRole("button", { name: "Revisa i confirma: Suport 24x7" })).toBeTruthy();

    // Desconegut: no és un «no».
    const unknown = facts.querySelector<HTMLElement>('[data-gap="unknown"]')!;
    expect(unknown.textContent).toContain("Sense cap dada (3)");
    expect(unknown.textContent).toContain("Operador inscrit a la CNMC");
    expect(unknown.textContent).toContain("No saber-ho no vol dir que no");
    expect(container.querySelector("#serveis")!.textContent).toContain("Centraleta i veu IP · Obra civil · Accessos FTTH");

    // Partner declarat amb el seu paper; solució només a l'històric.
    const partner = record(container, "partner-1");
    expect(partner.textContent).toContain("Subcontractista · Obra civil · Girona");
    expect(container.querySelector("#solucions")!.textContent).toContain("Van oferir la Centraleta Demo allotjada");
    expect(container.querySelector("#capacitats")!.textContent).toContain("Primer cal declarar una solució");
    expect(container.textContent).not.toMatch(RAW_CODES);
  });

  it("declara un fet operatiu directament: sense cap camp de verificació", async () => {
    post.mockResolvedValue({});
    await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    fireEvent.click(screen.getByRole("button", { name: "Declara un fet" }));

    const dialog = screen.getByRole("dialog", { name: "Declara un fet operatiu" });
    expect(dialog.textContent).toContain("Quedarà com a declarat i pendent d'evidència");
    expect(dialog.textContent).not.toMatch(/[Vv]erificat\b/);
    const key = within(dialog).getByLabelText(/^Fet/) as HTMLSelectElement;
    // El que ja està declarat no es pot tornar a declarar.
    expect([...key.options].map((option) => option.value)).not.toContain("datacenter_tier3");
    fireEvent.change(key, { target: { value: "telecom_operator_cnmc" } });
    fireEvent.change(within(dialog).getByLabelText(/Aclariment/), { target: { value: "Inscrits al registre" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();

    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/operating_fact`, {
      key: "telecom_operator_cnmc",
      value: true,
      detail: "Inscrits al registre",
      lifecycle: "current",
      valid_until: null,
    });
  });

  it("declara un partner amb el seu paper i edita una dada existent", async () => {
    post.mockResolvedValue({});
    patch.mockResolvedValue({});
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));

    fireEvent.click(screen.getByRole("button", { name: "Declara un partner" }));
    let dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: "Operador Demo" } });
    fireEvent.change(within(dialog).getByLabelText(/^Paper/), { target: { value: "wholesale_operator" } });
    fireEvent.change(within(dialog).getByLabelText(/Línia de servei/), { target: { value: "access_ftth" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/partner`, {
      name: "Operador Demo",
      tax_id: null,
      lifecycle: "current",
      valid_until: null,
      roles: [{ role: "wholesale_operator", service_line_code: "access_ftth", territory: null }],
    });

    fireEvent.click(within(record(container, "fact-tier3")).getByRole("button", { name: "Edita" }));
    dialog = screen.getByRole("dialog", { name: "Edita el fet operatiu" });
    expect((within(dialog).getByLabelText(/^Fet/) as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Situació/), { target: { value: "retired" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(patch).toHaveBeenCalledWith(`${BASE}/knowledge/records/operating_fact/fact-tier3`, {
      value: true,
      detail: "CPD en un centre Tier-3",
      lifecycle: "retired",
      valid_until: null,
    });
  });

  it("un partner es pot reanomenar i rebre un segon paper sobre la línia de telefonia mòbil", async () => {
    patch.mockResolvedValue({});
    post.mockResolvedValue({});
    respond({
      [`${BASE}/knowledge/vocabulary`]: {
        ...vocabularyFixture,
        service_lines: [...vocabularyFixture.service_lines, { code: "mobile_telephony", label: "Telefonia mobil" }],
      },
    });
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));

    // Canviar el nom: el mateix registre, sense tocar-ne la procedència.
    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Edita" }));
    let dialog = screen.getByRole("dialog", { name: "Edita el partner" });
    fireEvent.change(within(dialog).getByLabelText(/^Nom/), { target: { value: "Soci Demo Telecom" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(patch).toHaveBeenCalledWith(`${BASE}/knowledge/records/partner/partner-1`, {
      name: "Soci Demo Telecom",
      tax_id: null,
      lifecycle: "current",
      valid_until: null,
    });
    expect(patch.mock.calls[0][1]).not.toHaveProperty("as_of");
    expect(patch.mock.calls[0][1]).not.toHaveProperty("knowledge_origin");

    // Un segon paper sobre la línia nova, al mateix partner.
    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Afegeix un paper" }));
    dialog = screen.getByRole("dialog");
    const line = within(dialog).getByLabelText(/Línia de servei/) as HTMLSelectElement;
    expect([...line.options].map((option) => option.textContent)).toContain("Telefonia mòbil");
    fireEvent.change(within(dialog).getByLabelText(/^Paper/), { target: { value: "wholesale_operator" } });
    fireEvent.change(line, { target: { value: "mobile_telephony" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/partner_role`, {
      partner_id: "partner-1",
      role: "wholesale_operator",
      service_line_code: "mobile_telephony",
      territory: null,
      lifecycle: "current",
      valid_until: null,
    });
    // Cap segon partner: només s'ha creat un paper.
    expect(post.mock.calls.map(([path]) => path)).toEqual([`${BASE}/knowledge/records/partner_role`]);
  });

  it("declara una certificació amb norma, nivell i abast estructurat", async () => {
    post.mockResolvedValue({});
    await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    fireEvent.click(screen.getByRole("button", { name: "Declara una certificació" }));
    const dialog = screen.getByRole("dialog", { name: "Declara una certificació" });
    fireEvent.change(within(dialog).getByLabelText(/^Norma/), { target: { value: "ENS" } });
    fireEvent.change(within(dialog).getByLabelText(/^Nivell/), { target: { value: "high" } });
    fireEvent.click(within(dialog).getByLabelText("Obra civil"));
    fireEvent.submit(dialog.querySelector("form")!);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/certifications`, {
      name: "ENS Alt",
      standard_code: "ENS",
      level: "high",
      expires_at: null,
      lifecycle: "current",
      scope_service_lines: ["civil_works"],
    });
  });

  it("històric detectat: els candidats per categoria", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /Històric detectat/ }));
    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "Certificacions",
      "Fets operatius",
      "Solucions",
    ]);
    // D'on surt cada suggeriment: l'expedient i la data.
    expect(screen.getByText(/^Ajuntament de Mostra · .*2025$/)).toBeTruthy();
  });

  it("empresa nova, sense històric ni dades: res pendent, tot per declarar", async () => {
    respondEmpty();
    const { container } = await renderPage();
    expect(["verified", "declared", "pending", "historic", "unknown"].map((key) => tile(container, key))).toEqual(["0", "0", "0", "0", "10"]);
    // Sense feina pendent s'obre directament al coneixement actual.
    expect(screen.getByRole("tab", { name: "Coneixement actual" }).getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#certificacions")!.textContent).toContain("Sense cap dada (2)");
    expect(screen.getByText("Cap solució declarada.")).toBeTruthy();
    expect(screen.getByText("Cap producte declarat.")).toBeTruthy();
    expect(screen.getByText("Cap partner declarat.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Declara una capacitat" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: "Pendents de validar" }));
    expect(screen.getByRole("heading", { name: "Res pendent de validar" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Històric detectat" }));
    expect(screen.getByRole("heading", { name: "Cap dada detectada a l'històric" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Declara coneixement actual" }));
    expect(screen.getByRole("tab", { name: "Coneixement actual" }).getAttribute("aria-selected")).toBe("true");
  });

  it("un rol que no pot decidir ni verificar ho veu explicat, amb les accions desactivades", async () => {
    role = "technical";
    const { container } = await renderPage();
    expect(container.textContent).toContain("pot confirmar o descartar el que diu l'històric");
    expect(container.textContent).toContain("pot verificar una dada");
    expect((screen.getByRole("button", { name: "Revisa i confirma: Suport 24x7" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(record(container, "cert-ens")).getByRole("button", { name: "Verifica" }) as HTMLButtonElement).disabled).toBe(true);
    // Aportar evidència sí que pot.
    expect((within(record(container, "cert-ens")).getByRole("button", { name: "Afegeix evidència" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("qui només consulta ho veu tot, però no pot declarar, editar ni tocar l'evidència", async () => {
    role = "viewer";
    const { container } = await renderPage();
    expect(container.textContent).toContain("pots consultar el coneixement, però no declarar-lo ni modificar-lo");

    const ens = record(container, "cert-ens");
    expect((within(ens).getByRole("button", { name: "Edita l'abast" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(ens).getByRole("button", { name: "Verifica" }) as HTMLButtonElement).disabled).toBe(true);
    // L'evidència es pot mirar, en mode de lectura.
    expect(within(ens).queryByRole("button", { name: "Afegeix evidència" })).toBeNull();
    fireEvent.click(within(ens).getByRole("button", { name: "Evidència" }));
    expect(within(ens).getByTestId("evidence").getAttribute("data-readonly")).toBe("true");

    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    for (const name of ["Declara una certificació", "Declara un servei", "Declara un fet", "Declara una solució", "Declara un producte", "Declara un partner"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled, name).toBe(true);
    }
    expect((within(record(container, "fact-tier3")).getByRole("button", { name: "Edita" }) as HTMLButtonElement).disabled).toBe(true);
    expect(post).not.toHaveBeenCalled();
    expect(patch).not.toHaveBeenCalled();
  });

  it("el tècnic declara i edita, però no pot eliminar: es veu explicat i no hi ha el botó", async () => {
    role = "technical";
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    expect((screen.getByRole("button", { name: "Declara un fet" }) as HTMLButtonElement).disabled).toBe(false);
    // L'evidència la pot aportar.
    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Evidència" }));
    expect(within(record(container, "partner-1")).getByTestId("evidence").getAttribute("data-readonly")).toBe("false");

    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Edita" }));
    const dialog = screen.getByRole("dialog", { name: "Edita el partner" });
    expect(within(dialog).queryByRole("button", { name: "Elimina" })).toBeNull();
    expect(dialog.textContent).toContain("Eliminar-la definitivament és cosa de propietari o administració");
    expect(within(dialog).getByRole("button", { name: "Desa" })).toBeTruthy();
  });

  it("propietari: pot eliminar, amb una segona confirmació", async () => {
    remove.mockResolvedValue(undefined);
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Edita" }));
    const dialog = screen.getByRole("dialog", { name: "Edita el partner" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Elimina" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Segur? Elimina-ho definitivament" }));
    await flush();
    expect(remove).toHaveBeenCalledWith(`${BASE}/knowledge/records/partner/partner-1`);
  });

  it("editar una dada verificada avisa que tornarà a pendent de verificar; una de declarada, no", async () => {
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));

    fireEvent.click(within(record(container, "fact-tier3")).getByRole("button", { name: "Edita" }));
    let dialog = screen.getByRole("dialog", { name: "Edita el fet operatiu" });
    expect(within(dialog).getByRole("note").textContent).toContain("tornarà a «pendent de verificar»");
    expect(within(dialog).getByRole("note").textContent).toContain("l'evidència es conserva");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel·la" }));

    fireEvent.click(within(record(container, "partner-1")).getByRole("button", { name: "Edita" }));
    dialog = screen.getByRole("dialog", { name: "Edita el partner" });
    expect(within(dialog).queryByRole("note")).toBeNull();
  });

  it("després d'editar una dada verificada surt «Declarat · Pendent de verificar», no «Falta evidència»", async () => {
    // El que torna l'API un cop editada: declarada, amb l'evidència encara enllaçada.
    const snapshot = snapshotFixture();
    const claim = snapshot.facts[0].claims[0];
    snapshot.facts[0] = { ...snapshot.facts[0], source: "current_declared", accreditation_pending: true, claims: [{ ...claim, source: "current_declared", accreditation_pending: true, has_evidence: true }] };
    respond({ [`${BASE}/knowledge/snapshot`]: snapshot });
    const { container } = await renderPage();
    fireEvent.click(screen.getByRole("tab", { name: "Coneixement actual" }));
    const row = record(container, "fact-tier3");
    expect(within(row).getByText("Declarat")).toBeTruthy();
    expect(within(row).getByText("Pendent de verificar")).toBeTruthy();
    expect(within(row).queryByText("Falta evidència")).toBeNull();
    expect(within(row).queryByText("Verificat")).toBeNull();
    // I es pot tornar a verificar des de la mateixa fila.
    expect((within(row).getByRole("button", { name: "Verifica" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("canviar l'abast d'una certificació verificada també avisa", async () => {
    const snapshot = snapshotFixture();
    const claim = snapshot.certifications[0].claims[0];
    snapshot.certifications[0] = { ...snapshot.certifications[0], source: "current_verified", accreditation_pending: false, claims: [{ ...claim, source: "current_verified", accreditation_pending: false, has_evidence: true }] };
    respond({ [`${BASE}/knowledge/snapshot`]: snapshot });
    const { container } = await renderPage();
    // Verificada, però amb l'abast encara per estructurar: continua sent un pendent.
    fireEvent.click(within(record(container, "cert-ens")).getByRole("button", { name: "Edita l'abast" }));
    expect(within(screen.getByRole("dialog")).getByRole("note").textContent).toContain("Si en canvies l'abast tornarà a «pendent de verificar»");
  });

  it("si la fotografia no carrega, ho diu i deixa tornar-ho a provar", async () => {
    respond({ [`${BASE}/knowledge/snapshot`]: new Error("sense connexió") });
    render(<CompanyKnowledgePage />);
    expect(await screen.findByText("No hem pogut carregar el coneixement de l'empresa")).toBeTruthy();
    respond();
    fireEvent.click(screen.getByRole("button", { name: "Torna-ho a provar" }));
    expect(await screen.findByRole("tablist")).toBeTruthy();
  });
});
