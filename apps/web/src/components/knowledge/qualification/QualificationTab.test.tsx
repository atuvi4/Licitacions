import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { emptySnapshotFixture, legacyCertificationFixture, recordsFixture } from "@/test/knowledge-fixture";
import {
  equipmentFixture,
  financialYearsFixture,
  insurancesFixture,
  ipv4FactFixture,
  peopleFixture,
  projectFixture,
  qualificationSnapshotFixture,
  qualificationVocabularyFixture,
  registrationsFixture,
  servicesFixture,
} from "@/test/qualification-fixture";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const remove = vi.fn();
const replace = vi.fn();
let role = "owner";
let search = "tab=qualificacio";

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
  useSearchParams: () => new URLSearchParams(search),
}));
vi.mock("@/components/EvidencePanel", () => ({
  EvidencePanel: ({ entityType, entityId, readOnly }: { entityType: string; entityId: string; readOnly?: boolean }) => (
    <div data-testid="evidence" data-entity={`${entityType}:${entityId}`} data-readonly={readOnly ? "true" : "false"} />
  ),
}));

import CompanyKnowledgePage from "@/app/(app)/empresa/coneixement/page";

const BASE = "/companies/c1";
/** El que aquesta pantalla no diu mai: ni codis interns ni judicis de compliment. */
const FORBIDDEN = /current_verified|current_declared|pending_verification|employer_per_victim|sector_operator_registry|no compleix|bloqueig|perdràs punts|L'Escala|CNMC/i;

function respond(overrides: Record<string, unknown> = {}) {
  const map: Record<string, unknown> = {
    [`${BASE}/knowledge/snapshot`]: qualificationSnapshotFixture(),
    [`${BASE}/knowledge/observations?status=candidate`]: [],
    [`${BASE}/knowledge/records`]: { ...recordsFixture(), operating_facts: [...recordsFixture().operating_facts, ipv4FactFixture], equipment: equipmentFixture },
    [`${BASE}/certifications`]: [legacyCertificationFixture],
    [`${BASE}/services`]: servicesFixture,
    [`${BASE}/knowledge/vocabulary`]: qualificationVocabularyFixture,
    [`${BASE}/financial-years`]: financialYearsFixture,
    [`${BASE}/projects`]: [projectFixture()],
    [`${BASE}/insurances`]: insurancesFixture,
    [`${BASE}/registrations`]: registrationsFixture,
    [`${BASE}/people`]: peopleFixture,
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
    [`${BASE}/knowledge/records`]: { ...recordsFixture(), operating_facts: [], equipment: [] },
    [`${BASE}/financial-years`]: [],
    [`${BASE}/projects`]: [],
    [`${BASE}/insurances`]: [],
    [`${BASE}/registrations`]: [],
    [`${BASE}/people`]: [],
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

async function renderSection(section?: string) {
  search = section ? `tab=qualificacio&seccio=${section}` : "tab=qualificacio";
  const view = render(<CompanyKnowledgePage />);
  await screen.findByRole("navigation", { name: "Seccions de qualificació i mitjans" });
  await flush();
  return view;
}

function record(container: HTMLElement, id: string): HTMLElement {
  const row = container.querySelector<HTMLElement>(`[data-record="${id}"]`);
  if (!row) throw new Error(`No hi ha la fila ${id}`);
  return row;
}

function drawer(name: RegExp | string): HTMLElement {
  return screen.getByRole("dialog", { name });
}

function type(scope: HTMLElement, label: RegExp | string, value: string) {
  fireEvent.change(within(scope).getByLabelText(label), { target: { value } });
}

function save(scope: HTMLElement, label: RegExp = /^(Desa|Declara)$/) {
  fireEvent.click(within(scope).getByRole("button", { name: label }));
}

function pick(scope: HTMLElement, combobox: RegExp, typed: string, option: string) {
  fireEvent.change(within(scope).getByRole("combobox", { name: combobox }), { target: { value: typed } });
  fireEvent.click(within(scope).getByRole("option", { name: option }));
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  patch.mockReset();
  remove.mockReset();
  replace.mockReset();
  post.mockResolvedValue({ id: "new" });
  patch.mockResolvedValue({ id: "same" });
  remove.mockResolvedValue(undefined);
  role = "owner";
  respond();
});

describe("Qualificació i mitjans: estructura", () => {
  it("és una pestanya del coneixement, amb les set seccions i cap codi ni judici", async () => {
    const { container } = await renderSection();
    expect(screen.getByRole("tab", { name: "Qualificació i mitjans", selected: true })).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Seccions de qualificació i mitjans" });
    const titles = within(nav).getAllByRole("button").map((button) => button.querySelector("span")?.textContent);
    expect(titles).toEqual(["Solvència econòmica", "Experiència", "Assegurances", "Registres", "Persones", "Equips", "Recursos quantitatius"]);
    expect(within(nav).getByRole("button", { name: /Solvència econòmica/ }).getAttribute("aria-current")).toBe("true");
    expect(within(nav).getByRole("button", { name: /Solvència econòmica/ }).textContent).toContain("2 fitxes · 1 per verificar");
    expect(container.textContent).not.toMatch(FORBIDDEN);
  });

  it("canviar de secció la desa a la URL (?seccio=) sense tocar la pestanya", async () => {
    await renderSection();
    fireEvent.click(screen.getByRole("button", { name: /^Equips/ }));
    expect(replace).toHaveBeenCalledWith("/empresa/coneixement?tab=qualificacio&seccio=equips", { scroll: false });
    expect(screen.getByRole("heading", { name: "Equips" })).toBeTruthy();
  });

  it("solvència: estats humans (Verificat, Declarat, Falta evidència) i cap càlcul de solvència", async () => {
    const { container } = await renderSection("solvencia");
    const verified = record(container, "fy-2025");
    expect(within(verified).getByText("Exercici 2025")).toBeTruthy();
    expect(within(verified).getByText("Verificat")).toBeTruthy();
    expect(verified.textContent).toContain("Informació a");
    const declared = record(container, "fy-2024");
    expect(within(declared).getByText("Declarat")).toBeTruthy();
    expect(within(declared).getByText("Falta evidència")).toBeTruthy();
    expect(container.textContent).not.toMatch(/R1|compleix|mitjana|suficient/i);
  });

  it.each([
    ["solvencia", "No hi ha exercicis econòmics informats", "Afegeix un exercici"],
    ["experiencia", "No hi ha referències d'execució informades", "Afegeix una referència"],
    ["assegurances", "No hi ha assegurances informades", "Afegeix una assegurança"],
    ["registres", "No hi ha registres informats", "Afegeix un registre"],
    ["persones", "No hi ha persones informades", "Afegeix una persona"],
    ["equips", "No hi ha equips informats", "Afegeix un equip"],
  ])("buit de %s: diu què falta, sense judici, i com afegir-ho", async (section, title, action) => {
    respondEmpty();
    const { container } = await renderSection(section);
    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: action }).length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(FORBIDDEN);
  });

  it("recursos quantitatius: IPv4 assignables sense dada és desconegut, no «no»", async () => {
    respondEmpty();
    const { container } = await renderSection("recursos");
    const gap = container.querySelector('[data-gap="unknown"]') as HTMLElement;
    expect(within(gap).getByText("IPv4 assignables")).toBeTruthy();
    expect(gap.textContent).toContain("No saber-ho no vol dir que no");
  });
});

describe("Qualificació i mitjans: permisos (la UI no és la seguretat, però no ofereix el que no es pot fer)", () => {
  it("qui només consulta: cap alta ni edició, verificar desactivat i evidència de lectura", async () => {
    role = "viewer";
    const { container } = await renderSection("solvencia");
    expect(screen.queryByRole("button", { name: "Afegeix un exercici" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edita l'exercici/ })).toBeNull();
    const row = record(container, "fy-2024");
    expect((within(row).getByRole("button", { name: "Verifica" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(row).getByRole("button", { name: "Evidència" }));
    expect(within(row).getByTestId("evidence").getAttribute("data-readonly")).toBe("true");
  });

  it("qui només consulta, en un buit: el botó es veu desactivat amb qui ho pot fer", async () => {
    role = "viewer";
    respondEmpty();
    await renderSection("persones");
    expect((screen.getByRole("button", { name: "Afegeix una persona" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Només qui té el rol de Propietari\/ària, Administrador\/a, Responsable de candidatures o Tècnic\/a pot informar-ho/)).toBeTruthy();
  });

  it("tècnic/a: edita però no elimina ni verifica", async () => {
    role = "technical";
    const { container } = await renderSection("solvencia");
    expect((within(record(container, "fy-2024")).getByRole("button", { name: "Verifica" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Edita l'exercici 2024" }));
    const panel = drawer("Edita l'exercici 2024");
    expect(within(panel).queryByRole("button", { name: "Elimina" })).toBeNull();
    expect(within(panel).getByRole("button", { name: "Desa" })).toBeTruthy();
  });

  it("propietari/ària: verifica amb l'acció explícita i elimina amb confirmació", async () => {
    post.mockResolvedValue({});
    const { container } = await renderSection("solvencia");
    fireEvent.click(within(record(container, "fy-2024")).getByRole("button", { name: "Verifica" }));
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/financial_year/fy-2024/verify`, {});

    fireEvent.click(screen.getByRole("button", { name: "Edita l'exercici 2024" }));
    const panel = drawer("Edita l'exercici 2024");
    fireEvent.click(within(panel).getByRole("button", { name: "Elimina" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(panel).getByRole("button", { name: "Segur? Elimina-ho definitivament" }));
    await flush();
    expect(remove).toHaveBeenCalledWith(`${BASE}/financial-years/fy-2024`);
  });

  it("verificar sense evidència: s'explica i s'obre l'evidència, sense JSON", async () => {
    post.mockRejectedValue(new ApiError(422, "evidence_required", "Cal evidencia"));
    const { container } = await renderSection("equips");
    const row = record(container, "eq-1");
    fireEvent.click(within(row).getByRole("button", { name: "Verifica" }));
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/equipment/eq-1/verify`, {});
    expect(within(row).getByRole("alert").textContent).toMatch(/cal evidència enllaçada/);
    expect(within(row).getByTestId("evidence").getAttribute("data-entity")).toBe("company_equipment:eq-1");
  });
});

describe("Qualificació i mitjans: formularis", () => {
  it("exercici nou: sense data de vigència envia null i mai cap verificació", async () => {
    await renderSection("solvencia");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un exercici" }));
    const panel = drawer("Afegeix un exercici econòmic");
    expect((within(panel).getByLabelText(/Data de vigència de la informació/) as HTMLInputElement).value).toBe("");
    type(panel, /Any de l'exercici/, "2023");
    type(panel, /Xifra de negoci/, "1.234.567,89");
    save(panel);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/financial-years`, {
      year: 2023,
      revenue: "1234567.89",
      currency: "EUR",
      as_of: null,
      notes: null,
    });
    expect(JSON.stringify(post.mock.calls)).not.toContain("verification_status");
  });

  it("un import mal escrit no s'envia i es diu per què", async () => {
    await renderSection("solvencia");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un exercici" }));
    const panel = drawer("Afegeix un exercici econòmic");
    type(panel, /Any de l'exercici/, "2023");
    type(panel, /Xifra de negoci/, "-5");
    save(panel);
    expect(post).not.toHaveBeenCalled();
    expect(within(panel).getByText(/Import no vàlid/)).toBeTruthy();
  });

  it("editar una dada verificada avisa que tornarà a pendent", async () => {
    await renderSection("solvencia");
    fireEvent.click(screen.getByRole("button", { name: "Edita l'exercici 2025" }));
    expect(within(drawer("Edita l'exercici 2025")).getByRole("note").textContent).toMatch(/tornarà a «pendent de verificar»/);
  });

  it("l'error de l'API es llegeix en paraules, mai en JSON", async () => {
    post.mockRejectedValue(
      new ApiError(422, "validation_error", "Les dades enviades no son valides.", [{ field: "", message: "Value error, Un any nomes pot tenir un import executat.", type: "value_error" }]),
    );
    await renderSection("solvencia");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un exercici" }));
    const panel = drawer("Afegeix un exercici econòmic");
    type(panel, /Any de l'exercici/, "2023");
    save(panel);
    await flush();
    expect(panel.textContent).toContain("Les dades enviades no son valides. Un any nomes pot tenir un import executat.");
    expect(panel.textContent).not.toMatch(/Value error|[{]"/);
  });

  it("experiència: el repetidor d'imports executats aplica les regles de l'API abans d'enviar", async () => {
    const { container } = await renderSection("experiencia");
    const row = record(container, "prj-1");
    expect(row.textContent).toContain("Executat per exercici");
    // Les línies que acredita, no el servei del catàleg (legacy).
    expect(row.textContent).toContain("Accessos FTTH");
    expect(row.textContent).not.toContain("Desplegament de fibra");
    fireEvent.click(within(row).getByRole("button", { name: "Edita Manteniment de xarxa de prova" }));
    const panel = drawer("Edita la referència d'execució");
    expect(panel.textContent).toContain(
      "Indica l'import realment executat en cada exercici. No és l'import ofert ni necessàriament l'import total del contracte.",
    );
    expect((within(panel).getByLabelText("Any de la fila 1") as HTMLInputElement).value).toBe("2024");
    expect((within(panel).getByLabelText("Import executat de la fila 1") as HTMLInputElement).value).toBe("30000");

    // Any repetit.
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix un exercici" }));
    type(panel, "Any de la fila 3", "2024");
    type(panel, "Import executat de la fila 3", "100");
    save(panel);
    expect(patch).not.toHaveBeenCalled();
    expect(within(panel).getByText(/L'any 2024 ja hi és/)).toBeTruthy();

    // Fora de les dates del projecte.
    type(panel, "Any de la fila 3", "2027");
    expect(within(panel).getByText(/L'any 2027 cau fora de les dates del projecte/)).toBeTruthy();

    // La suma passa de l'import total (90.000).
    type(panel, "Any de la fila 3", "2026");
    type(panel, "Import executat de la fila 3", "40.000,01");
    save(panel);
    expect(patch).not.toHaveBeenCalled();
    expect(within(panel).getByText("Els imports executats sumen més que l'import total del projecte.")).toBeTruthy();

    // Bé: s'envia el conjunt sencer, ordenat, amb les línies del vocabulari triades.
    type(panel, "Import executat de la fila 3", "40.000");
    fireEvent.click(within(panel).getByRole("button", { name: "Treu la fila 2" }));
    pick(panel, /Línies de servei que acredita/, "veu", "Centraleta i veu IP");
    save(panel);
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    const [path, body] = patch.mock.calls[0];
    expect(path).toBe(`${BASE}/projects/prj-1`);
    expect(body).toMatchObject({
      annual_amounts: [
        { year: 2024, amount: "30000" },
        { year: 2026, amount: "40000" },
      ],
      service_line_codes: ["access_ftth", "voice_pbx"],
      status: "in_progress",
      as_of: null,
    });
    expect(body).not.toHaveProperty("verification_status");
    expect(body).not.toHaveProperty("service_ids");
  });

  it("experiència: les línies surten del vocabulari encara que l'empresa no tingui cap servei", async () => {
    respond({ [`${BASE}/services`]: [] });
    await renderSection("experiencia");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix una referència" }));
    const panel = drawer("Afegeix una referència d'execució");
    type(panel, /Nom o objecte del contracte/, "Contracte històric de prova");
    pick(panel, /Línies de servei que acredita/, "obra", "Obra civil");
    pick(panel, /Línies de servei que acredita/, "veu", "Centraleta i veu IP");
    fireEvent.click(within(panel).getByRole("button", { name: "Treu Obra civil" }));
    save(panel);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/projects`, expect.objectContaining({ service_line_codes: ["voice_pbx"], annual_amounts: [] }));
  });

  it("assegurances: un únic «Desa» envia la fitxa amb el conjunt complet de límits", async () => {
    const { container } = await renderSection("assegurances");
    const row = record(container, "ins-rc");
    expect(row.textContent).toContain("RC patronal per víctima");
    expect(row.textContent).toContain("Cobertura total");
    fireEvent.click(within(row).getByRole("button", { name: /Edita l'assegurança/ }));
    await flush();
    const panel = drawer("Edita l'assegurança");
    type(panel, "Import del límit de la fila 1", "700.000");
    fireEvent.click(within(panel).getByRole("button", { name: "Treu el límit de la fila 2" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix un límit" }));
    fireEvent.change(within(panel).getByLabelText("Tipus de límit de la fila 2"), { target: { value: "per_claim" } });
    type(panel, "Import del límit de la fila 2", "300000");
    save(panel);
    await flush();
    // Una sola crida: cap POST/PATCH/DELETE de límits per separat.
    expect(patch).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(patch).toHaveBeenCalledWith(
      `${BASE}/insurances/ins-rc`,
      expect.objectContaining({
        type: "civil_liability",
        as_of: null,
        limits: [
          { kind: "total", amount: "700000", currency: "EUR", notes: null },
          { kind: "per_claim", amount: "300000", currency: "EUR", notes: null },
        ],
      }),
    );
    expect(get.mock.calls.some(([path]) => String(path).includes("/limits"))).toBe(false);
  });

  it("assegurances: si el servidor refusa la fitxa, el calaix no es tanca i ho diu", async () => {
    patch.mockRejectedValue(new ApiError(409, "conflict", "Ja existeix un registre equivalent."));
    const { container } = await renderSection("assegurances");
    fireEvent.click(within(record(container, "ins-rc")).getByRole("button", { name: /Edita l'assegurança/ }));
    const panel = drawer("Edita l'assegurança");
    save(panel);
    await flush();
    expect(drawer("Edita l'assegurança").textContent).toContain("Ja existeix un registre equivalent.");
  });

  it("assegurances: un límit sense tipus o repetit no s'envia", async () => {
    await renderSection("assegurances");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix una assegurança" }));
    const panel = drawer("Afegeix una assegurança");
    const options = within(panel).queryAllByRole("option").map((option) => option.textContent);
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix un límit" }));
    const kinds = Array.from((within(panel).getByLabelText("Tipus de límit de la fila 1") as HTMLSelectElement).options).map((option) => option.textContent);
    expect(kinds).toContain("RC patronal per víctima");
    expect(options.join(" ")).not.toMatch(/Escala/);
    type(panel, "Import del límit de la fila 1", "1000");
    save(panel);
    expect(post).not.toHaveBeenCalled();
    expect(within(panel).getByText("Tria quin límit és.")).toBeTruthy();
  });

  it("registres: emissor buit (cap valor proposat) i abast de línies cercable; sense línies, null", async () => {
    const { container } = await renderSection("registres");
    expect(record(container, "reg-1").textContent).toContain("Abast pendent d'estructurar");
    expect(record(container, "reg-1").textContent).toContain("Abast desconegut");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un registre" }));
    const panel = drawer("Afegeix un registre");
    expect((within(panel).getByLabelText(/Emissor/) as HTMLInputElement).value).toBe("");
    type(panel, /Nom del registre/, "Inscripció de prova");
    save(panel);
    expect(post).not.toHaveBeenCalled();
    expect(within(panel).getByText("Tria el tipus de registre.")).toBeTruthy();
    pick(panel, /Tipus de registre/, "sectorial", "Registre sectorial d'operadors");
    save(panel);
    await flush();
    expect(post).toHaveBeenLastCalledWith(`${BASE}/registrations`, expect.objectContaining({ type: "sector_operator_registry", issuer: null, scope_service_lines: null, as_of: null }));

    post.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Edita Registre sectorial de prova" }));
    const edit = drawer("Edita el registre");
    pick(edit, /Abast: línies de servei inscrites/, "FTTH", "Accessos FTTH");
    pick(edit, /Abast: línies de servei inscrites/, "veu", "Centraleta i veu IP");
    fireEvent.click(within(edit).getByRole("button", { name: "Treu Accessos FTTH" }));
    save(edit);
    await flush();
    expect(patch).toHaveBeenCalledWith(`${BASE}/registrations/reg-1`, expect.objectContaining({ scope_service_lines: ["voice_pbx"], lifecycle: "current" }));
  });

  it("persones: perfils del vocabulari i «activa» com a cicle de vida, sense assignació a plecs", async () => {
    const { container } = await renderSection("persones");
    expect(record(container, "person-1").textContent).toContain("Cap de projecte");
    fireEvent.click(screen.getByRole("button", { name: "Edita Persona Prova" }));
    const panel = drawer("Edita la persona");
    expect(panel.textContent).not.toMatch(/licitació|plec/i);
    pick(panel, /Perfils professionals/, "camp", "Tècnic/a de camp");
    fireEvent.click(within(panel).getByLabelText("Activa a l'empresa"));
    save(panel);
    await flush();
    expect(patch).toHaveBeenCalledWith(`${BASE}/people/person-1`, {
      first_name: "Persona",
      last_name: "Prova",
      role: "Coordinació tècnica",
      profile_codes: ["project_manager", "field_technician"],
      as_of: null,
      lifecycle: "retired",
    });
  });

  it("equips: tipus cercable, calibratge coherent i sense data per defecte", async () => {
    await renderSection("equips");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un equip" }));
    const panel = drawer("Afegeix un equip");
    expect((within(panel).getByLabelText(/Calibratge vàlid fins al/) as HTMLInputElement).disabled).toBe(true);
    pick(panel, /Tipus d'equip/, "fusio", "Fusionadora de fibra");
    fireEvent.change(within(panel).getByLabelText("Calibratge"), { target: { value: "yes" } });
    type(panel, /Calibratge vàlid fins al/, "2027-02-01");
    fireEvent.change(within(panel).getByLabelText("Com se'n disposa"), { target: { value: "rented" } });
    save(panel);
    await flush();
    const [path, body] = post.mock.calls[0];
    expect(path).toBe(`${BASE}/knowledge/records/equipment`);
    expect(body).toEqual({
      equipment_type: "fusion_splicer",
      manufacturer: null,
      model: null,
      serial_reference: null,
      access_mode: "rented",
      quantity: 1,
      calibration_required: true,
      calibration_valid_until: "2027-02-01",
      notes: null,
    });
    expect(body).not.toHaveProperty("as_of");
  });

  it("IPv4 assignables: enter no negatiu, amb el fet quantitatiu (mai un sí/no)", async () => {
    respond({ [`${BASE}/knowledge/records`]: { ...recordsFixture(), equipment: [] } });
    await renderSection("recursos");
    fireEvent.click(screen.getByRole("button", { name: "Informa" }));
    const panel = drawer("Informa: IPv4 assignables");
    type(panel, /IPv4 assignables \(adreces\)/, "-3");
    save(panel);
    expect(post).not.toHaveBeenCalled();
    expect(within(panel).getByText(/nombre enter/)).toBeTruthy();
    type(panel, /IPv4 assignables \(adreces\)/, "256");
    save(panel);
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/knowledge/records/operating_fact`, { key: "ipv4_assignable_count", value: true, value_number: 256, detail: null });
  });

  it("IPv4 informat: es mostra amb el seu estat i no surt a «Fets operatius»", async () => {
    const { container } = await renderSection("recursos");
    const row = record(container, "fact-ipv4");
    expect(row.textContent).toContain("IPv4 assignables");
    expect(row.textContent).toContain("256 adreces");
    expect(within(row).getByText("Declarat")).toBeTruthy();

    search = "tab=actual";
    const current = render(<CompanyKnowledgePage />);
    await flush();
    const facts = current.container.querySelector("#fets") as HTMLElement;
    expect(facts.textContent).not.toContain("IPv4");
  });
});

describe("Qualificació i mitjans: pendents de validar", () => {
  it("les fitxes noves surten a «Pendents» amb el seu nom, no amb el codi", async () => {
    const snapshot = qualificationSnapshotFixture();
    snapshot.pending_validation = [
      { kind: "accreditation_pending", subject: "execution_reference", key: "prj-1", detail: "", record_id: "prj-1", observation_id: null, superseded_by_current: false },
      { kind: "accreditation_pending", subject: "person", key: "project_manager", detail: "", record_id: "person-1", observation_id: null, superseded_by_current: false },
    ];
    respond({ [`${BASE}/knowledge/snapshot`]: snapshot });
    search = "tab=pendents";
    const { container } = render(<CompanyKnowledgePage />);
    await screen.findByRole("tablist");
    await flush();
    const project = record(container, "prj-1");
    expect(within(project).getByText("Manteniment de xarxa de prova")).toBeTruthy();
    expect(project.textContent).toContain("Experiència");
    expect(within(record(container, "person-1")).getByText("Persona Prova")).toBeTruthy();
    expect(container.textContent).not.toMatch(/execution_reference/);
  });
});

describe("Qualificació i mitjans: dades que encara no compten", () => {
  it("una persona sense perfils: «Sense perfil de qualificació», què cal fer, i cap «Verifica»", async () => {
    respond({
      [`${BASE}/people`]: [{ ...peopleFixture[0], id: "person-2", first_name: "Altra", last_name: "Persona", profile_codes: null }],
    });
    const { container } = await renderSection("persones");
    const row = record(container, "person-2");
    expect(within(row).getByText("Sense perfil de qualificació")).toBeTruthy();
    expect(row.textContent).toContain("Afegeix almenys un perfil perquè aquesta persona pugui comptar com a mitjà professional.");
    expect(row.textContent).not.toContain("No compta");
    expect(within(row).queryByRole("button", { name: "Verifica" })).toBeNull();
  });

  it("equips i IPv4: la data de vigència és opcional i no promet un buit (la data d'alta)", async () => {
    await renderSection("equips");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix un equip" }));
    const panel = drawer("Afegeix un equip");
    const asOf = within(panel).getByLabelText(/Data de vigència de la informació/) as HTMLInputElement;
    expect(asOf.value).toBe("");
    expect(panel.textContent).toContain("Si no indiques cap data, Tuvi considerarà la data d'alta.");
  });
});

describe("Qualificació i mitjans: CPV del projecte", () => {
  it("la llista i el calaix mostren els CPV, separats de les línies acreditades", async () => {
    const { container } = await renderSection("experiencia");
    const row = record(container, "prj-1");
    expect(row.textContent).toContain("CPV:");
    expect(row.textContent).toContain("64200000");
    fireEvent.click(within(row).getByRole("button", { name: "Edita Manteniment de xarxa de prova" }));
    const panel = drawer("Edita la referència d'execució");
    const cpvs = within(panel).getByRole("group", { name: "CPV del projecte" });
    expect(cpvs.textContent).toContain("64200000 · Serveis de telecomunicacions");
    expect(cpvs.textContent).toContain("No són les línies de servei acreditades");
    expect(within(panel).getByRole("group", { name: "Línies de servei que acredita" })).not.toBe(cpvs);
  });

  it("afegeix un CPV per l'API, en rellegeix l'estat i no ho barreja amb el «Desa»", async () => {
    post.mockResolvedValue({ ...projectFixture(), cpvs: [
      { cpv_code: "64200000", description: "Serveis de telecomunicacions" },
      { cpv_code: "32500000", description: "Equip de telecomunicacions" },
    ] });
    await renderSection("experiencia");
    fireEvent.click(screen.getByRole("button", { name: "Edita Manteniment de xarxa de prova" }));
    const panel = drawer("Edita la referència d'execució");
    const calls = get.mock.calls.length;
    type(panel, "Codi CPV", " 32500000 ");
    type(panel, "Descripció (opcional)", "Equip de telecomunicacions");
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix" }));
    await flush();
    expect(post).toHaveBeenCalledWith(`${BASE}/projects/prj-1/cpvs`, { cpv_code: "32500000", description: "Equip de telecomunicacions" });
    expect(within(panel).getByRole("group", { name: "CPV del projecte" }).textContent).toContain("32500000");
    // L'estat (verificació inclosa) surt del servidor: es rellegeix, no es simula.
    expect(get.mock.calls.length).toBeGreaterThan(calls);
    expect(patch).not.toHaveBeenCalled();
  });

  it("treu un CPV", async () => {
    remove.mockResolvedValue({ ...projectFixture(), cpvs: [] });
    await renderSection("experiencia");
    fireEvent.click(screen.getByRole("button", { name: "Edita Manteniment de xarxa de prova" }));
    const panel = drawer("Edita la referència d'execució");
    fireEvent.click(within(panel).getByRole("button", { name: "Treu el CPV 64200000" }));
    await flush();
    expect(remove).toHaveBeenCalledWith(`${BASE}/projects/prj-1/cpvs/64200000`);
    expect(within(panel).getByText("Cap CPV assignat a aquest projecte.")).toBeTruthy();
  });

  it("un codi no vàlid o repetit no s'envia, i l'error de l'API es llegeix en paraules", async () => {
    await renderSection("experiencia");
    fireEvent.click(screen.getByRole("button", { name: "Edita Manteniment de xarxa de prova" }));
    const panel = drawer("Edita la referència d'execució");
    type(panel, "Codi CPV", "6420");
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix" }));
    expect(within(panel).getByText(/Codi CPV no vàlid/)).toBeTruthy();
    type(panel, "Codi CPV", "64200000");
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix" }));
    expect(within(panel).getByText("El CPV 64200000 ja és al projecte.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();

    post.mockRejectedValue(new ApiError(409, "conflict", "Aquest CPV ja esta associat al projecte."));
    type(panel, "Codi CPV", "32500000");
    fireEvent.click(within(panel).getByRole("button", { name: "Afegeix" }));
    await flush();
    expect(panel.textContent).toContain("Aquest CPV ja esta associat al projecte.");
    expect(panel.textContent).not.toMatch(/[{]"/);
  });

  it("sense permís d'edició els CPV es veuen però no es poden afegir ni treure", async () => {
    const { ProjectCpvEditor } = await import("./ProjectCpvEditor");
    render(<ProjectCpvEditor projectId="prj-1" initial={projectFixture().cpvs} canEdit={false} onChanged={() => {}} />);
    expect(screen.getByText("64200000")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Afegeix" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Treu el CPV/ })).toBeNull();
    expect(screen.queryByLabelText("Codi CPV")).toBeNull();
  });

  it("una alta nova no té CPV fins que es desa", async () => {
    await renderSection("experiencia");
    fireEvent.click(screen.getByRole("button", { name: "Afegeix una referència" }));
    const panel = drawer("Afegeix una referència d'execució");
    expect(panel.textContent).toContain("Els CPV del projecte s'hi poden afegir un cop desat.");
    expect(within(panel).queryByLabelText("Codi CPV")).toBeNull();
  });

  it("l'enllaç antic d'un projecte obre el seu calaix i treu el paràmetre de la URL", async () => {
    search = "tab=qualificacio&seccio=experiencia&project=prj-1";
    render(<CompanyKnowledgePage />);
    await screen.findByRole("dialog", { name: "Edita la referència d'execució" });
    expect(replace).toHaveBeenCalledWith("/empresa/coneixement?tab=qualificacio&seccio=experiencia", { scroll: false });
  });

  it("l'enllaç antic, per a qui només consulta, porta a la llista sense obrir cap calaix", async () => {
    role = "viewer";
    search = "tab=qualificacio&seccio=experiencia&project=prj-1";
    const { container } = render(<CompanyKnowledgePage />);
    await screen.findByRole("navigation", { name: "Seccions de qualificació i mitjans" });
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(record(container, "prj-1").textContent).toContain("64200000");
  });
});
