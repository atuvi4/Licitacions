import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readinessFixture } from "@/test/readiness-fixture";
import { lineFixture, lineInternalFixture, lotFixture, offerFixture, redactedOffer, sectionFixture, totalsFixture } from "@/test/economic-fixture";
import { workspaceFixture } from "@/test/workspace-fixture";
import type { EconomicOffer } from "@/lib/economic";

/* ----- Estat dels mocks ---------------------------------------------------- */

let role = "bid_manager";
let search = "pas=preparar&area=oferta";
let offer: EconomicOffer | null = offerFixture();
let tenderLots: Array<{ id: string; lot_number: number | null; title: string | null }> = [];

const post = vi.fn();
const patch = vi.fn();
const getReadiness = vi.fn();
const getOffer = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const get = async (path: string) => {
    if (path.includes("/final-readiness")) {
      getReadiness(path);
      return readinessFixture();
    }
    if (path.endsWith("/economic-offer")) {
      getOffer(path);
      if (!offer) throw new actual.ApiError(404, "not_found", "Sense oferta");
      return offer;
    }
    if (path.endsWith("/lots")) return tenderLots;
    throw new Error(`GET inesperat: ${path}`);
  };
  return {
    ...actual,
    api: { ...actual.api, get, post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args) },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    membership: { id: "m1", company_id: "c1", role, is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/candidatures/w1",
  useSearchParams: () => new URLSearchParams(search),
}));

import { ApiError } from "@/lib/api";

import { EconomicOfferSection } from "./EconomicOfferSection";
import { ReadinessProvider } from "./ReadinessProvider";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const registerLeaveGuard = vi.fn();

async function mount() {
  const view = render(
    <ReadinessProvider companyId="c1" workspaceId="w1">
      <EconomicOfferSection workspace={workspaceFixture()} registerLeaveGuard={registerLeaveGuard} />
    </ReadinessProvider>,
  );
  await flush();
  return view;
}

function click(name: RegExp | string, scope: HTMLElement = document.body) {
  return act(async () => {
    fireEvent.click(within(scope).getByRole("button", { name }));
  });
}

function rowOf(text: string): HTMLElement {
  return screen.getAllByText(text)[0].closest("tr") as HTMLElement;
}

/** Tot el text visible, per comprovar que una dada no hi és de cap manera. */
function pageText(): string {
  return document.body.innerHTML;
}

beforeEach(() => {
  role = "bid_manager";
  search = "pas=preparar&area=oferta";
  offer = offerFixture();
  tenderLots = [];
  post.mockReset();
  patch.mockReset();
  getReadiness.mockReset();
  getOffer.mockReset();
  registerLeaveGuard.mockReset();
  Element.prototype.scrollIntoView = vi.fn();
});

/* ----- Lectura i estructura ------------------------------------------------ */

describe("Oferta econòmica · lectura", () => {
  it("carrega l'oferta de l'endpoint real", async () => {
    await mount();
    expect(getOffer).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer");
  });

  it("sense oferta: estat inicial amb l'acció d'obrir-la", async () => {
    offer = null;
    await mount();
    expect(screen.getByText("Encara no hi ha oferta econòmica")).toBeTruthy();
    const button = screen.getByRole("button", { name: /Obre l'oferta econòmica/ });
    post.mockResolvedValue({ created: true, offer: offerFixture() });
    await click(/Obre l'oferta econòmica/);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer", {});
    expect(getReadiness).toHaveBeenCalledTimes(2);
    expect(button).toBeTruthy();
  });

  it("la jerarquia és lot → secció → línia, amb subtotals i total del lot", async () => {
    await mount();
    expect(screen.getByText("Lot 2 · Xarxa d'accés")).toBeTruthy();
    expect(screen.getAllByText("Equipament").length).toBeGreaterThan(0);
    expect(screen.getByText("Commutadors d'accés 48p")).toBeTruthy();
    expect(screen.getByText("Total del lot")).toBeTruthy();
  });

  it("els totals són els del servidor, formatats", async () => {
    await mount();
    const text = document.body.textContent!.replace(/ /g, " ");
    expect(text).toContain("11.100,00");
    expect(text).toContain("240.000,00");
  });

  it("el subtotal d'una línia surt del seu bloc de totals", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ totals: totalsFixture({ total_revenue: "3800.00" }) })] })] })],
    });
    await mount();
    expect(document.body.textContent!.replace(/ /g, " ")).toContain("3.800,00");
  });

  it("durada heretada, personalitzada i pendent es distingeixen", async () => {
    offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture({
              lines: [
                lineFixture({ id: "a", concept: "Heretada", pricing_basis: "monthly", duration_months_override: null, effective_duration_months: 12 }),
                lineFixture({ id: "b", concept: "Pròpia", pricing_basis: "monthly", duration_months_override: 24, effective_duration_months: 24 }),
                lineFixture({ id: "c", concept: "Pendent", pricing_basis: "monthly", duration_months_override: null, effective_duration_months: null }),
              ],
            }),
          ],
        }),
      ],
    });
    await mount();
    expect(rowOf("Heretada").textContent).toContain("Heretada del lot");
    expect(rowOf("Pròpia").textContent).toContain("Personalitzada");
    const pending = rowOf("Pendent");
    expect(pending.textContent).toContain("Durada pendent");
    expect(pending.textContent).not.toContain("0 mesos");
  });

  it("IVA sense definir es diu, i no s'hi suposa cap tipus", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ vat_rate: null })] })] })],
    });
    await mount();
    expect(rowOf("Commutadors d'accés 48p").textContent).toContain("IVA sense definir");
    expect(rowOf("Commutadors d'accés 48p").textContent).not.toContain("21");
  });

  it("el que bloqueja es pinta com a bloqueig, no com a avís", async () => {
    // `economic_offer_price_missing` i `economic_duration_missing` són
    // blockers a S6, igual que superar el màxim: han de llegir-se igual.
    offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture({
              lines: [
                lineFixture({ id: "a", concept: "Sense preu ofert", offered_unit_price_ex_vat: null }),
                lineFixture({ id: "b", concept: "Sense durada", pricing_basis: "monthly", effective_duration_months: null }),
                lineFixture({ id: "c", concept: "Amb IVA pendent", vat_rate: null }),
              ],
            }),
          ],
        }),
      ],
    });
    await mount();
    expect(rowOf("Sense preu ofert").querySelector(".text-danger")?.textContent).toBe("Sense preu");
    expect(rowOf("Sense durada").querySelector(".text-danger")?.textContent).toBe("Durada pendent");
    // L'IVA sense definir sí que és un avís.
    const vat = rowOf("Amb IVA pendent");
    expect(vat.querySelector(".text-warning")?.textContent).toBe("IVA sense definir");
    expect(vat.querySelector(".text-danger")).toBeNull();
  });

  it("màxim del plec: conegut, superat i desconegut", async () => {
    offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture({
              lines: [
                lineFixture({ id: "a", concept: "Dins", exceeds_unit_max: false, tender_max_unit_price_ex_vat: "1900.0000" }),
                lineFixture({ id: "b", concept: "Fora", exceeds_unit_max: true, tender_max_unit_price_ex_vat: "1900.0000" }),
                lineFixture({ id: "c", concept: "Sense màxim", exceeds_unit_max: null, tender_max_unit_price_ex_vat: null }),
              ],
            }),
          ],
        }),
      ],
    });
    await mount();
    expect(rowOf("Dins").textContent).toContain("Màxim del plec");
    const over = rowOf("Fora");
    expect(over.textContent).toContain("Supera el màxim del plec");
    expect(over.querySelector(".text-danger")).not.toBeNull();
    // Desconegut no és ni avís ni «no el supera».
    const unknown = rowOf("Sense màxim");
    expect(unknown.textContent).not.toContain("Màxim del plec");
    expect(unknown.textContent).not.toContain("Supera");
  });

  it("pressupost desconegut es diu, sense buscar-ne cap altre de semblant", async () => {
    offer = offerFixture({
      lots: [lotFixture({ limit: { tender_max: null, difference_to_maximum: null, exceeds_lot_max: null } })],
    });
    await mount();
    expect(document.body.textContent).toContain("No consta");
  });

  it("una secció sense línies ho diu sense inventar cap gravetat", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [], totals: totalsFixture({ line_count: 0, total_revenue: null }) })] })],
    });
    await mount();
    expect(screen.getAllByText("Equipament").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/bloqueig/i);
  });
});

/* ----- Veredicte contra el màxim (auditoria UI P1-01) ----------------------- */

describe("Oferta econòmica · veredicte contra el màxim en tres estats", () => {
  const emptyLot = () =>
    lotFixture({
      totals: totalsFixture({ line_count: 0, total_revenue: null }),
      limit: { tender_max: "204232.75", difference_to_maximum: null, exceeds_lot_max: null },
      sections: [sectionFixture({ lines: [], totals: totalsFixture({ line_count: 0, total_revenue: null }) })],
    });

  it("una oferta sense línies no diu mai «Sota el màxim»: diu que està pendent", async () => {
    offer = offerFixture({ totals: totalsFixture({ line_count: 0, total_revenue: null }), lots: [emptyLot()] });
    await mount();
    expect(document.body.textContent).not.toContain("Sota el màxim");
    expect(document.body.textContent).toContain("Pendent de calcular: falten línies o imports");
  });

  it("amb el màxim conegut i un total calculat per sota, diu «Sota el màxim»", async () => {
    await mount();
    expect(document.body.textContent).toContain("Sota el màxim");
    expect(document.body.textContent).not.toContain("Pendent de calcular");
  });

  it("per sobre del màxim, diu «Supera el pressupost» amb nivell de perill", async () => {
    offer = offerFixture({
      // L'API envia màxim − total: negatiu quan el supera.
      lots: [lotFixture({ limit: { tender_max: "1000.00", difference_to_maximum: "-250.00", exceeds_lot_max: true } })],
    });
    await mount();
    expect(screen.getByText("Supera el pressupost")).toBeTruthy();
    expect(document.body.querySelector(".text-danger")).not.toBeNull();
    expect(document.body.textContent).not.toContain("Sota el màxim");
  });

  it("amb diversos lots, un lot sense total queda pendent i no surt com a correcte", async () => {
    offer = offerFixture({
      lots: [lotFixture(), { ...emptyLot(), id: "lot2", tender_lot_id: "tl3", label: "Lot 3 · Veu", position: 1 }],
    });
    tenderLots = [
      { id: "tl2", lot_number: 2, title: "Xarxa d'accés" },
      { id: "tl3", lot_number: 3, title: "Veu" },
    ];
    await mount();
    // Cada lot amb el seu veredicte: el de línies, sota el màxim; el buit, pendent.
    const lotLine = (amount: string) =>
      [...document.querySelectorAll("p")].find((p) => p.textContent?.includes(`Pressupost del lot ${amount}`))!;
    expect(lotLine("240.000,00").textContent).toContain("Sota el màxim");
    expect(lotLine("204.232,75").textContent).toContain("Pendent de calcular: falten línies o imports");
    expect(lotLine("204.232,75").textContent).not.toContain("Sota el màxim");
  });

  it("un lot amb una secció amb preu i una altra encara buida no diu «Sota el màxim» (revisió del patch)", async () => {
    // L'API suma només les seccions amb línies: el total i `exceeds_lot_max: false` surten de la que té preu.
    offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture(),
            sectionFixture({ id: "s-buida", title: "Equipament pendent", lines: [], totals: totalsFixture({ line_count: 0, total_revenue: null }) }),
          ],
        }),
      ],
    });
    await mount();
    expect(document.body.textContent).not.toContain("Sota el màxim");
    expect(document.body.textContent).toContain("Pendent de calcular: falten línies o imports");
  });
});

/* ----- Permisos i redacció -------------------------------------------------- */

describe("Oferta econòmica · permisos i redacció", () => {
  for (const authorized of ["owner", "admin", "bid_manager"]) {
    it(`${authorized} veu cost i marge`, async () => {
      role = authorized;
      await mount();
      expect(screen.getByRole("columnheader", { name: "Cost intern" })).toBeTruthy();
      expect(screen.getByRole("columnheader", { name: "Marge" })).toBeTruthy();
    });
  }

  for (const redacted of ["technical", "viewer"]) {
    it(`${redacted}: cap columna interna, cap control d'edició, cap dada de cost al DOM`, async () => {
      role = redacted;
      offer = redactedOffer();
      await mount();

      expect(screen.queryByRole("columnheader", { name: "Cost intern" })).toBeNull();
      expect(screen.queryByRole("columnheader", { name: "Marge" })).toBeNull();
      expect(screen.queryByRole("columnheader", { name: "Acció" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Edita" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Arxiva" })).toBeNull();
      expect(screen.queryByRole("button", { name: /Afegeix una secció/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /Afegeix una línia/ })).toBeNull();

      // I les xifres internes no són enlloc del marcatge, ni amagades.
      const html = pageText();
      for (const secret of ["1520", "9120", "1980", "17,8", "21,7", "Cost intern", "Marge"]) {
        expect(html).not.toContain(secret);
      }
      expect(screen.getAllByText(/Només qui té el rol de/).length).toBe(1);
    });
  }

  it("el rol redactat continua veient el que sí que pot: preu, subtotal i pressupost", async () => {
    role = "technical";
    offer = redactedOffer();
    await mount();
    const text = document.body.textContent!.replace(/ /g, " ");
    expect(text).toContain("1.850,00");
    expect(text).toContain("11.100,00");
    expect(text).toContain("240.000,00");
  });
});

/* ----- Edició --------------------------------------------------------------- */

describe("Oferta econòmica · edició", () => {
  it("s'obre l'editor a la fila, i només un alhora", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ id: "a", concept: "Primera" }), lineFixture({ id: "b", concept: "Segona" })] })] })],
    });
    await mount();
    await click("Edita", rowOf("Primera"));
    expect(screen.getByLabelText(/Concepte/)).toBeTruthy();
    await click("Edita", rowOf("Segona"));
    expect(screen.getAllByLabelText(/Concepte/)).toHaveLength(1);
    expect((screen.getByLabelText(/Concepte/) as HTMLInputElement).value).toBe("Segona");
  });

  it("escriure no desa res ni mou els totals", async () => {
    await mount();
    const before = document.body.textContent!.replace(/ /g, " ");
    expect(before).toContain("11.100,00");
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "9999" } });
    await flush();
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText("Canvis sense desar")).toBeTruthy();
    expect(screen.getByText("No inclou els canvis oberts")).toBeTruthy();
    // El total de dalt segueix sent el desat.
    expect(document.body.textContent!.replace(/ /g, " ")).toContain("11.100,00");
  });

  it("Desa envia expected_revision i només els camps canviats", async () => {
    patch.mockResolvedValue(
      offerFixture({
        revision: 5,
        lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ offered_unit_price_ex_vat: "1900.0000" })] })] })],
      }),
    );
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "1900" } });
    await click("Desa");
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    const [path, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/economic-offer/lines/ln1");
    expect(body).toEqual({ offered_unit_price_ex_vat: "1900", expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Desat")).toBeTruthy();
  });

  it("la revisió nova s'adopta per a la mutació següent", async () => {
    patch.mockResolvedValue(offerFixture({ revision: 9 }));
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "1900" } });
    await click("Desa");
    await flush();
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "1950" } });
    await click("Desa");
    await flush();
    expect((patch.mock.calls[1][1] as Record<string, unknown>).expected_revision).toBe(9);
  });

  it("Cancel·la plega sense desar", async () => {
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    await click("Cancel·la");
    expect(screen.queryByLabelText(/Concepte/)).toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("amb canvis oberts, obrir-ne una altra pregunta primer", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ id: "a", concept: "Primera" }), lineFixture({ id: "b", concept: "Segona" })] })] })],
    });
    await mount();
    await click("Edita", rowOf("Primera"));
    fireEvent.change(screen.getByLabelText(/Concepte/), { target: { value: "Bruta" } });
    await flush();
    expect(registerLeaveGuard).toHaveBeenLastCalledWith(expect.any(Function));
    await click("Edita", rowOf("Segona"));
    expect(screen.getByRole("dialog").textContent).toContain("Canvis sense desar");
    await click("Cancel·la", screen.getByRole("dialog"));
    expect((screen.getByLabelText(/Concepte/) as HTMLInputElement).value).toBe("Bruta");
  });

  it("crea una línia a la secció que toca", async () => {
    post.mockResolvedValue(offerFixture({ revision: 5 }));
    await mount();
    const fila = screen.getAllByRole("listitem").find((node) => node.textContent?.includes("Equipament"))!;
    await click("Afegeix una línia", fila);
    fireEvent.change(screen.getByLabelText(/Concepte/), { target: { value: "Fibra" } });
    fireEvent.change(screen.getByLabelText(/Quantitat/), { target: { value: "3" } });
    await click("Desa");
    await flush();
    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/economic-offer/sections/sec1/lines");
    expect(body).toEqual({ concept: "Fibra", quantity: "3", pricing_basis: "one_off", expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("amb una línia bruta, afegir secció pregunta ABANS d'obrir el diàleg", async () => {
    // Si preguntés en desar, la confirmació quedaria sota el modal de secció.
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "1900" } });
    await flush();
    await click(/Afegeix una secció/);
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0].textContent).toContain("Canvis sense desar");
    expect(screen.queryByLabelText(/Títol/)).toBeNull();
  });

  it("crea una secció", async () => {
    post.mockResolvedValue(offerFixture({ revision: 5 }));
    await mount();
    await click(/Afegeix una secció/);
    fireEvent.change(screen.getByLabelText(/Títol/), { target: { value: "Implantació" } });
    await click("Crea-la");
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer/lots/lot1/sections", {
      title: "Implantació",
      expected_revision: 4,
    });
  });

  it("arxiva una línia, i no hi ha cap esborrat", async () => {
    post.mockResolvedValue(offerFixture({ revision: 5 }));
    await mount();
    const row = rowOf("Commutadors d'accés 48p");
    expect(within(row).queryByRole("button", { name: /Elimina|Esborra/ })).toBeNull();
    await click("Arxiva", row);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer/lines/ln1/archive", { expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("les línies arxivades no es veuen fins que es demanen", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ id: "a", concept: "Viva" }), lineFixture({ id: "b", concept: "Vella", archived_at: "2026-09-14T09:00:00Z" })] })] })],
    });
    await mount();
    expect(screen.queryByText("Vella")).toBeNull();
    await click(/Mostra els arxivats/);
    expect(screen.getByText("Vella")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Restaura/ })).toBeNull();
  });

  it("un error normal es queda a la fila i conserva el que s'ha escrit", async () => {
    patch.mockRejectedValue(new ApiError(422, "validation_error", "Dada invàlida"));
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "-5" } });
    await click("Desa");
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("Dada invàlida");
    expect((screen.getByLabelText(/Preu ofert/) as HTMLInputElement).value).toBe("-5");
    expect(screen.queryByText(/s'ha modificat des que vas començar/)).toBeNull();
  });
});

/* ----- Conflicte 409 -------------------------------------------------------- */

describe("Oferta econòmica · conflicte de revisió", () => {
  const conflict = () => new ApiError(409, "economic_offer_revision_conflict", "L'oferta ha canviat mentre editaves: torna a carregar-la.");

  async function provoke() {
    patch.mockRejectedValue(conflict());
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "590" } });
    // El servidor ja té un altre valor quan es torna a llegir.
    offer = offerFixture({
      revision: 7,
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ offered_unit_price_ex_vat: "660.0000" })] })] })],
    });
    await click("Desa");
    await flush();
  }

  it("un 409 obre el flux de conflicte, no un error genèric", async () => {
    await provoke();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("s'ha modificat des que vas començar a editar");
    expect(dialog.textContent).not.toContain("No s'ha pogut desar");
  });

  it("es veuen les dues versions i no es fusiona res", async () => {
    await provoke();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("El que has escrit tu");
    expect(dialog.textContent).toContain("El valor actual del servidor");
    const text = dialog.textContent!.replace(/ /g, " ");
    expect(text).toContain("590,00");
    expect(text).toContain("660,00");
    expect(dialog.textContent).toContain("No es fusiona res");
    expect(within(dialog).queryByRole("button", { name: /Força|Sobreescriu|Fusiona/ })).toBeNull();
  });

  it("no s'inventa qui ho ha canviat ni quan", async () => {
    await provoke();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).not.toMatch(/Anna|Marc|fa \d+ minut|fa \d+ hor/);
  });

  it("es torna a llegir el servidor i no es desa res", async () => {
    await provoke();
    expect(getOffer).toHaveBeenCalledTimes(2);
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("«Recarrega i revisa» porta la versió del servidor a l'editor, sense desar", async () => {
    await provoke();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((screen.getByLabelText(/Preu ofert/) as HTMLInputElement).value).toBe("660.0000");
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("«Descarta els meus canvis» tanca l'editor i no dispara cap mutació", async () => {
    await provoke();
    await click("Descarta els meus canvis", screen.getByRole("dialog"));
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByLabelText(/Preu ofert/)).toBeNull();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
    const text = document.body.textContent!.replace(/ /g, " ");
    expect(text).toContain("660,00");
  });

  it("la revisió que es fa servir després del conflicte és la nova", async () => {
    await provoke();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    patch.mockResolvedValue(offerFixture({ revision: 8 }));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "700" } });
    await click("Desa");
    await flush();
    expect((patch.mock.calls[1][1] as Record<string, unknown>).expected_revision).toBe(7);
  });
});

/* ----- Lots ----------------------------------------------------------------- */

describe("Oferta econòmica · lots", () => {
  it("candidatura d'un lot: no hi ha selecció de lots", async () => {
    offer = offerFixture({ can_select_lots: false });
    await mount();
    expect(screen.queryByText("Lots als quals ens presentem")).toBeNull();
  });

  it("plec sense lots: el lot global es pinta igual", async () => {
    offer = offerFixture({ can_select_lots: false, lots: [lotFixture({ tender_lot_id: null, label: "Oferta" })] });
    await mount();
    expect(screen.getByText("Oferta")).toBeTruthy();
  });

  it("cas C: comença sense cap lot i no se'n tria cap sol", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [] });
    tenderLots = [
      { id: "tl1", lot_number: 1, title: "Obra" },
      { id: "tl2", lot_number: 2, title: "Xarxa" },
    ];
    await mount();
    expect(screen.getByText("Encara no s'ha triat cap lot.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("cas C: triar un lot fa el POST real i invalida la preparació", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [] });
    tenderLots = [{ id: "tl1", lot_number: 1, title: "Obra" }];
    post.mockResolvedValue({ created: true, offer: offerFixture({ can_select_lots: true, revision: 5 }) });
    await mount();
    await click(/Afegeix un lot/);
    await click(/Presenta-t/);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer/lots/from-tender-lot", {
      tender_lot_id: "tl1",
      expected_revision: 4,
    });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("cas C: un lot ja triat no es torna a oferir", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [lotFixture({ tender_lot_id: "tl2" })] });
    tenderLots = [
      { id: "tl1", lot_number: 1, title: "Obra" },
      { id: "tl2", lot_number: 2, title: "Xarxa" },
    ];
    await mount();
    await click(/Afegeix un lot/);
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Obra");
    expect(dialog.textContent).not.toContain("Xarxa");
  });

  it("arxivar un lot passa per l'endpoint d'arxivar i invalida la preparació", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [lotFixture({ id: "lotX" })] });
    post.mockResolvedValue(offerFixture({ can_select_lots: true, revision: 5, lots: [] }));
    await mount();
    // La llista de lots triats: l'única on el lot surt com a element de llista.
    const item = screen.getAllByRole("listitem").find((node) => node.textContent?.includes("Lot 2")) as HTMLElement;
    await click("Arxiva", item);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer/lots/lotX/archive", { expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("un lot arxivat no és actiu i no es configura", async () => {
    offer = offerFixture({ lots: [lotFixture({ archived_at: "2026-09-14T09:00:00Z" })] });
    await mount();
    expect(screen.queryByText("Equipament")).toBeNull();
    expect(screen.getByText("Aquesta oferta encara no té cap lot actiu.")).toBeTruthy();
  });

  it("l'etiqueta del lot és la del servidor, no es recalcula", async () => {
    offer = offerFixture({ lots: [lotFixture({ label: "Lot 7 · el que digui el servidor", tender_lot_id: "tl9" })] });
    await mount();
    expect(screen.getByText("Lot 7 · el que digui el servidor")).toBeTruthy();
  });
});

/* ----- Enllaços i preparació ------------------------------------------------ */

describe("Oferta econòmica · enllaços i preparació", () => {
  it("?entity= d'una línia l'obre, la fa visible i hi porta el focus", async () => {
    search = "pas=preparar&area=oferta&entity=ln1";
    await mount();
    expect(screen.getByLabelText(/Concepte/)).toBeTruthy();
    const highlighted = document.querySelector("[data-highlight='true']") as HTMLElement;
    expect(highlighted).toBeTruthy();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    expect(highlighted.contains(document.activeElement)).toBe(true);
    expect(highlighted.textContent).toContain("Línia enllaçada");
  });

  it("un problema global arriba sense entity i la pantalla s'obre normal", async () => {
    await mount();
    expect(screen.queryByLabelText(/Concepte/)).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("una entity desconeguda no peta: ho diu i deixa l'oferta sencera", async () => {
    search = "pas=preparar&area=oferta&entity=no-hi-es";
    await mount();
    expect(screen.getByRole("status").textContent).toContain("no hi és");
    expect(screen.getByText("Commutadors d'accés 48p")).toBeTruthy();
  });

  it("la pantalla no fabrica cap problema de preparació final", async () => {
    offer = offerFixture({
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ offered_unit_price_ex_vat: null, vat_rate: null, exceeds_unit_max: true })] })] })],
    });
    await mount();
    // Diu què li falta a la línia, però el veredicte segueix sent el d'S6.
    expect(document.body.textContent).toContain("Sense preu");
    expect(document.body.textContent).toContain("IVA sense definir");
    expect(document.body.textContent).not.toMatch(/bloqueig|bloquejos/i);
  });
});

describe("Oferta econòmica · l'esborrany en conflicte no es perd", () => {
  const conflict = () => new ApiError(409, "economic_offer_revision_conflict", "L'oferta ha canviat mentre editaves.");

  /** Revisió N al servidor, esborrany local, PATCH amb expected_revision=N i 409. */
  async function upToConflict() {
    expect(offer!.revision).toBe(4);
    patch.mockRejectedValue(conflict());
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "590" } });
    offer = offerFixture({
      revision: 7,
      lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ offered_unit_price_ex_vat: "660.0000" })] })] })],
    });
    await click("Desa");
    await flush();
    expect((patch.mock.calls[0][1] as Record<string, unknown>).expected_revision).toBe(4);
  }

  it("el cicle sencer: conflicte, recàrrega, servidor a l'editor i esborrany encara consultable", async () => {
    await upToConflict();

    // 5. El modal ensenya les dues versions.
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent!.replace(/\u00a0/g, " ")).toContain("590,00");
    expect(dialog.textContent!.replace(/\u00a0/g, " ")).toContain("660,00");

    // 6-7. Recarrega i revisa: l'editor passa a ser el del servidor.
    await click("Recarrega i revisa", dialog);
    await flush();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((screen.getByLabelText(/Preu ofert/) as HTMLInputElement).value).toBe("660.0000");

    // 8. I el que havia escrit encara es pot consultar.
    const previous = screen.getByText("El teu esborrany anterior").closest("details") as HTMLElement;
    expect(previous.textContent!.replace(/\u00a0/g, " ")).toContain("590,00");

    // 9. Cap PATCH automàtic.
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("l'esborrany anterior no s'aboca sol als camps del servidor", async () => {
    await upToConflict();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    await flush();
    // El camp és el del servidor, no una barreja.
    expect((screen.getByLabelText(/Preu ofert/) as HTMLInputElement).value).toBe("660.0000");
    expect((screen.getByLabelText(/Preu ofert/) as HTMLInputElement).value).not.toBe("590");
  });

  it("només desapareix quan es descarta explícitament", async () => {
    await upToConflict();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    await flush();
    expect(screen.getByText("El teu esborrany anterior")).toBeTruthy();

    await click(/Descarta l'esborrany anterior/);
    await flush();
    expect(screen.queryByText("El teu esborrany anterior")).toBeNull();
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("o quan un desat nou acaba bé", async () => {
    await upToConflict();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    await flush();
    expect(screen.getByText("El teu esborrany anterior")).toBeTruthy();

    patch.mockResolvedValue(
      offerFixture({
        revision: 8,
        lots: [lotFixture({ sections: [sectionFixture({ lines: [lineFixture({ offered_unit_price_ex_vat: "590.0000" })] })] })],
      }),
    );
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "590" } });
    await click("Desa");
    await flush();
    expect((patch.mock.calls[1][1] as Record<string, unknown>).expected_revision).toBe(7);
    expect(screen.queryByText("El teu esborrany anterior")).toBeNull();
  });

  it("o quan es cancel·la l'intent", async () => {
    await upToConflict();
    await click("Recarrega i revisa", screen.getByRole("dialog"));
    await flush();
    await click("Cancel·la");
    await flush();
    expect(screen.queryByText("El teu esborrany anterior")).toBeNull();
    expect(screen.queryByLabelText(/Preu ofert/)).toBeNull();
  });
});

describe("Oferta econòmica · 409 estructurals", () => {
  const conflict = () => new ApiError(409, "economic_offer_revision_conflict", "L'oferta ha canviat mentre editaves.");

  it("crear una secció amb 409: ni reintent, ni P8, i el títol escrit es conserva", async () => {
    post.mockRejectedValue(conflict());
    await mount();
    await click(/Afegeix una secció/);
    fireEvent.change(screen.getByLabelText(/Títol/), { target: { value: "Implantació" } });
    await click("Crea-la");
    await flush();

    expect((post.mock.calls[0][1] as Record<string, unknown>).expected_revision).toBe(4);
    expect(post).toHaveBeenCalledTimes(1);
    // No és el diàleg de conflicte de línia.
    expect(screen.queryByText(/El que has escrit tu/)).toBeNull();
    expect(document.body.textContent).toContain("L'oferta ha canviat des que la vas obrir");
    // El formulari conserva el que s'havia escrit.
    expect((screen.getByLabelText(/Títol/) as HTMLInputElement).value).toBe("Implantació");
    // S'ha rellegit l'oferta, i la preparació NO s'ha donat per actualitzada.
    expect(getOffer).toHaveBeenCalledTimes(2);
    expect(getReadiness).toHaveBeenCalledTimes(1);
  });

  it("arxivar una línia amb 409: es rellegeix i no se sobreescriu res", async () => {
    post.mockRejectedValue(conflict());
    await mount();
    await click("Arxiva", rowOf("Commutadors d'accés 48p"));
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect((post.mock.calls[0][1] as Record<string, unknown>).expected_revision).toBe(4);
    expect(document.body.textContent).toContain("Res del que acabes de demanar no s'ha desat");
    expect(getOffer).toHaveBeenCalledTimes(2);
    expect(getReadiness).toHaveBeenCalledTimes(1);
  });

  it("triar un lot amb 409: mateix tracte", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [] });
    tenderLots = [{ id: "tl1", lot_number: 1, title: "Obra" }];
    post.mockRejectedValue(conflict());
    await mount();
    await click(/Afegeix un lot/);
    await click(/Presenta-t/);
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect((post.mock.calls[0][1] as Record<string, unknown>).expected_revision).toBe(4);
    expect(screen.queryByText(/El que has escrit tu/)).toBeNull();
    expect(document.body.textContent).toContain("L'oferta ha canviat des que la vas obrir");
    expect(getReadiness).toHaveBeenCalledTimes(1);
  });

  it("arxivar un lot amb 409: es rellegeix i el lot segueix on era", async () => {
    offer = offerFixture({ can_select_lots: true, lots: [lotFixture({ id: "lotX" })] });
    post.mockRejectedValue(conflict());
    await mount();
    const item = screen.getAllByRole("listitem").find((node) => node.textContent?.includes("Lot 2")) as HTMLElement;
    await click("Arxiva", item);
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect(getOffer).toHaveBeenCalledTimes(2);
    expect(getReadiness).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText("Lot 2 · Xarxa d'accés").length).toBeGreaterThan(0);
  });

  it("un 409 estructural amb un editor obert i net no obre el flux de línia", async () => {
    post.mockRejectedValue(conflict());
    await mount();
    // Editor obert, sense cap canvi.
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    await click(/Afegeix una secció/);
    fireEvent.change(screen.getByLabelText(/Títol/), { target: { value: "Nova" } });
    await click("Crea-la");
    await flush();
    expect(screen.queryByText(/El que has escrit tu/)).toBeNull();
    expect(document.body.textContent).toContain("L'oferta ha canviat des que la vas obrir");
  });
});

describe("Oferta econòmica · durada del lot", () => {
  /** La fila de la durada: el bloc que porta l'etiqueta «Durada del lot». */
  const durationRow = () =>
    [...document.querySelectorAll("div")].filter((node) => node.textContent?.startsWith("Durada del lot")).at(-1) as HTMLElement;

  /** El formulari obert de la durada, per prémer-hi Desa sense ambigüitat. */
  const durationForm = () => screen.getByLabelText(/Durada del lot en mesos/).closest("form") as HTMLElement;

  it("ensenya la durada que diu el servidor", async () => {
    await mount();
    expect(durationRow().textContent).toContain("12 mesos");
  });

  it("sense durada ho diu, i no s'hi suposa cap número", async () => {
    offer = offerFixture({ lots: [lotFixture({ duration_months: null })] });
    await mount();
    const row = durationRow();
    expect(row.textContent).toContain("Sense definir");
    expect(row.textContent).not.toContain("12");
    expect(row.textContent).not.toContain("0 mesos");
  });

  it("un rol autoritzat la pot editar", async () => {
    await mount();
    expect(within(durationRow()).getByRole("button", { name: "Edita la durada" })).toBeTruthy();
  });

  for (const redacted of ["technical", "viewer"]) {
    it(`${redacted} no té cap control de durada`, async () => {
      role = redacted;
      offer = redactedOffer();
      await mount();
      expect(screen.queryByRole("button", { name: "Edita la durada" })).toBeNull();
      // Però continua veient quina és.
      expect(durationRow().textContent).toContain("12 mesos");
    });
  }

  it("desar envia duration_months i expected_revision", async () => {
    patch.mockResolvedValue(offerFixture({ revision: 5, lots: [lotFixture({ duration_months: 24 })] }));
    await mount();
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "24" } });
    await click("Desa", durationForm());
    await flush();
    const [path, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/economic-offer/lots/lot1");
    expect(body).toEqual({ duration_months: 24, expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("buidar-la envia null, no zero ni dotze", async () => {
    patch.mockResolvedValue(offerFixture({ revision: 5, lots: [lotFixture({ duration_months: null })] }));
    await mount();
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "  " } });
    await click("Desa", durationForm());
    await flush();
    const [, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).toEqual({ duration_months: null, expected_revision: 4 });
    expect(durationRow().textContent).toContain("Sense definir");
  });

  it("la revisió nova s'adopta, i les línies recurrents reben la durada del servidor", async () => {
    offer = offerFixture({
      lots: [
        lotFixture({
          duration_months: null,
          sections: [
            sectionFixture({
              lines: [lineFixture({ concept: "Manteniment", pricing_basis: "monthly", effective_duration_months: null })],
            }),
          ],
        }),
      ],
    });
    await mount();
    expect(rowOf("Manteniment").textContent).toContain("Durada pendent");

    // El servidor torna l'oferta sencera amb la durada resolta.
    patch.mockResolvedValue(
      offerFixture({
        revision: 9,
        lots: [
          lotFixture({
            duration_months: 12,
            sections: [
              sectionFixture({
                lines: [lineFixture({ concept: "Manteniment", pricing_basis: "monthly", effective_duration_months: 12 })],
              }),
            ],
          }),
        ],
      }),
    );
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "12" } });
    await click("Desa", durationForm());
    await flush();

    const row = rowOf("Manteniment");
    expect(row.textContent).toContain("12 mesos");
    expect(row.textContent).toContain("Heretada del lot");
    expect(row.textContent).not.toContain("Durada pendent");

    // I la revisió següent ja és la nova.
    patch.mockResolvedValue(offerFixture({ revision: 10 }));
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "18" } });
    await click("Desa", durationForm());
    await flush();
    expect((patch.mock.calls[1][1] as Record<string, unknown>).expected_revision).toBe(9);
  });

  it("els totals no es mouen fins que respon el servidor", async () => {
    await mount();
    const before = document.body.textContent!.replace(/\u00a0/g, " ");
    expect(before).toContain("11.100,00");
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "36" } });
    await flush();
    expect(patch).not.toHaveBeenCalled();
    expect(document.body.textContent!.replace(/\u00a0/g, " ")).toContain("11.100,00");
  });

  it("un 409 és estructural: ni reintent ni P8, i el que s'ha escrit es queda", async () => {
    patch.mockRejectedValue(new ApiError(409, "economic_offer_revision_conflict", "L'oferta ha canviat."));
    await mount();
    await click("Edita la durada");
    fireEvent.change(screen.getByLabelText(/Durada del lot en mesos/), { target: { value: "24" } });
    await click("Desa", durationForm());
    await flush();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/El que has escrit tu/)).toBeNull();
    expect(document.body.textContent).toContain("L'oferta ha canviat des que la vas obrir");
    expect((screen.getByLabelText(/Durada del lot en mesos/) as HTMLInputElement).value).toBe("24");
  });
});

describe("Oferta econòmica · seccions", () => {
  const sectionRow = () => screen.getAllByRole("listitem").find((node) => node.textContent?.includes("Equipament"))!;
  /** El formulari obert de renombrar. */
  const renameForm = () => screen.getByLabelText(/Títol de la secció/).closest("form") as HTMLElement;

  it("renombrar i arxivar només per a qui pot editar", async () => {
    await mount();
    const row = sectionRow();
    expect(within(row).getByRole("button", { name: "Renombra" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Arxiva" })).toBeTruthy();
    // I mai «Elimina».
    expect(within(row).queryByRole("button", { name: /Elimina|Esborra/ })).toBeNull();
  });

  for (const redacted of ["technical", "viewer"]) {
    it(`${redacted} no veu cap control de secció`, async () => {
      role = redacted;
      offer = redactedOffer();
      await mount();
      expect(screen.queryByRole("button", { name: "Renombra" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Arxiva" })).toBeNull();
      expect(screen.queryByRole("button", { name: /Afegeix una secció/ })).toBeNull();
    });
  }

  it("renombrar fa el PATCH real amb expected_revision i invalida la preparació", async () => {
    patch.mockResolvedValue(offerFixture({ revision: 5 }));
    await mount();
    await click("Renombra", sectionRow());
    fireEvent.change(screen.getByLabelText(/Títol de la secció/), { target: { value: "Equipament de xarxa" } });
    await click("Desa", renameForm());
    await flush();
    const [path, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/workspaces/w1/economic-offer/sections/sec1");
    expect(body).toEqual({ title: "Equipament de xarxa", expected_revision: 4 });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("un títol buit no s'envia", async () => {
    await mount();
    await click("Renombra", sectionRow());
    fireEvent.change(screen.getByLabelText(/Títol de la secció/), { target: { value: "   " } });
    await flush();
    expect((within(renameForm()).getByRole("button", { name: "Desa" }) as HTMLButtonElement).disabled).toBe(true);
    // I enviant el formulari amb Enter, que no passa pel botó, tampoc no surt.
    await act(async () => {
      fireEvent.submit(renameForm());
    });
    await flush();
    expect(patch).not.toHaveBeenCalled();
  });

  it("arxivar demana confirmació i després crida l'endpoint real", async () => {
    post.mockResolvedValue(offerFixture({ revision: 5 }));
    await mount();
    await click("Arxiva", sectionRow());
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Arxiva la secció");
    expect(dialog.textContent).toContain("Les seves línies es conserven");
    await click("Arxiva-la", dialog);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/economic-offer/sections/sec1/archive", {
      expected_revision: 4,
    });
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("cancel·lar la confirmació no arxiva res", async () => {
    await mount();
    await click("Arxiva", sectionRow());
    await click("Cancel·la", screen.getByRole("dialog"));
    expect(post).not.toHaveBeenCalled();
  });

  it("una secció arxivada no es veu fins que es demana, i no es pot restaurar", async () => {
    offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture({ id: "s1", title: "Viva" }),
            sectionFixture({ id: "s2", title: "Vella", archived_at: "2026-09-14T09:00:00Z" }),
          ],
        }),
      ],
    });
    await mount();
    expect(screen.queryByText("Vella")).toBeNull();
    await click(/Mostra els arxivats/);
    expect(screen.getAllByText("Vella").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Restaura/ })).toBeNull();
  });

  it("un 409 en arxivar no força res ni reintenta", async () => {
    post.mockRejectedValue(new ApiError(409, "economic_offer_revision_conflict", "L'oferta ha canviat."));
    await mount();
    await click("Arxiva", sectionRow());
    await click("Arxiva-la", screen.getByRole("dialog"));
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/El que has escrit tu/)).toBeNull();
    expect(document.body.textContent).toContain("L'oferta ha canviat des que la vas obrir");
  });

  it("amb una línia bruta, renombrar pregunta abans i no apila diàlegs", async () => {
    await mount();
    await click("Edita", rowOf("Commutadors d'accés 48p"));
    fireEvent.change(screen.getByLabelText(/Preu ofert/), { target: { value: "1900" } });
    await flush();
    await click("Renombra", sectionRow());
    const dialogs = screen.getAllByRole("dialog");
    expect(dialogs).toHaveLength(1);
    expect(dialogs[0].textContent).toContain("Canvis sense desar");
    expect(screen.queryByLabelText(/Títol de la secció/)).toBeNull();
  });
});


/* ----- Objectiu comercial i sincronització (S3-S8b) ------------------------ */

/** Una oferta amb una línia que té cost, preu i, si es vol, objectiu. */
function offerWithLine(line: Partial<Parameters<typeof lineFixture>[0]> = {}) {
  return offerFixture({
    lots: [
      lotFixture({
        sections: [
          sectionFixture({
            lines: [
              lineFixture({
                id: "ln1",
                concept: "Terminals IP",
                offered_unit_price_ex_vat: "140.0000",
                internal_economics: lineInternalFixture({
                  unit_cost_ex_vat: "100.0000",
                }),
                ...line,
              }),
            ],
          }),
        ],
      }),
    ],
  });
}

describe("Oferta econòmica · objectiu comercial", () => {
  it("un rol econòmic veu l'objectiu, el preu proposat i l'estat", async () => {
    offer = offerWithLine({
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: "100.0000",
        target_markup_pct: "20.0000",
        proposed_unit_price_ex_vat: "120.0000",
        price_matches_target: false,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));

    expect(screen.getByText("Objectiu comercial")).toBeTruthy();
    expect(screen.getByLabelText(/^Objectiu de markup/)).toBeTruthy();
    expect(screen.getByText("El preu ofert no és l'objectiu")).toBeTruthy();
  });

  it("editar l'objectiu no toca el camp del preu ofert", async () => {
    offer = offerWithLine({
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: "100.0000",
        target_markup_pct: "20.0000",
        proposed_unit_price_ex_vat: "120.0000",
        price_matches_target: false,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));

    const price = screen.getByLabelText(/^Preu ofert/) as HTMLInputElement;
    const before = price.value;

    await act(async () => {
      fireEvent.change(screen.getByLabelText(/^Objectiu de markup/), {
        target: { value: "30" },
      });
    });

    expect((screen.getByLabelText(/^Preu ofert/) as HTMLInputElement).value).toBe(before);
  });

  it("aplicar l'objectiu és una acció separada i explícita", async () => {
    offer = offerWithLine({
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: "100.0000",
        target_markup_pct: "20.0000",
        proposed_unit_price_ex_vat: "120.0000",
        price_matches_target: false,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));
    await click("Aplica objectiu");

    expect(post).toHaveBeenCalledWith(
      "/companies/c1/workspaces/w1/economic-offer/lines/ln1/apply-target",
      { expected_revision: offer!.revision },
    );
  });

  it("avisa que substituirà el preu escrit a mà", async () => {
    offer = offerWithLine({
      offered_unit_price_ex_vat: "140.0000",
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: "100.0000",
        target_markup_pct: "20.0000",
        proposed_unit_price_ex_vat: "120.0000",
        price_matches_target: false,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));

    expect(screen.getByText(/Substituirà el preu ofert actual/)).toBeTruthy();
  });

  it("amb el preu ja a l'objectiu no ofereix aplicar-lo", async () => {
    offer = offerWithLine({
      offered_unit_price_ex_vat: "120.0000",
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: "100.0000",
        target_markup_pct: "20.0000",
        proposed_unit_price_ex_vat: "120.0000",
        price_matches_target: true,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));

    expect(screen.getByText("El preu ofert ja és l'objectiu")).toBeTruthy();
    const apply = screen.getByRole("button", { name: "Aplica objectiu" });
    expect(apply.hasAttribute("disabled")).toBe(true);
  });

  it("sense cost ni objectiu, l'estat és «sense determinar»", async () => {
    offer = offerWithLine({
      internal_economics: lineInternalFixture({
        unit_cost_ex_vat: null,
        target_markup_pct: null,
        proposed_unit_price_ex_vat: null,
        price_matches_target: null,
      }),
    });
    await mount();
    await click("Edita", rowOf("Terminals IP"));

    expect(screen.getByText("Sense determinar")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Aplica objectiu" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("qui no pot veure el cost tampoc no veu l'objectiu", async () => {
    /**
     * Un `technical` no té ni el botó d'editar una línia, que és una garantia
     * més forta que amagar el bloc: no hi arriba per cap camí. I els números
     * tampoc no viatgen —`redactedOffer` treu `internal_economics` sencer, com
     * fa el servidor.
     */
    role = "technical";
    offer = redactedOffer(
      offerWithLine({
        internal_economics: lineInternalFixture({
          target_markup_pct: "20.0000",
          proposed_unit_price_ex_vat: "120.0000",
        }),
      }),
    );
    await mount();

    expect(within(rowOf("Terminals IP")).queryByRole("button", { name: "Edita" })).toBeNull();
    expect(screen.queryByText("Objectiu comercial")).toBeNull();
    expect(screen.queryByLabelText(/^Objectiu de markup/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Aplica objectiu" })).toBeNull();
    expect(pageText()).not.toContain("120.0000");
    expect(pageText()).not.toContain("20.0000");
  });
});

describe("Oferta econòmica · l'opció ha canviat", () => {
  it("una línia divergida ho diu sense estil d'alarma", async () => {
    offer = offerWithLine({
      solution_option_id: "op1",
      diverged_fields: ["supplier_name", "unit_cost_ex_vat"],
    });
    await mount();

    const notice = screen.getByRole("button", { name: /L'opció ha canviat/ });
    expect(notice).toBeTruthy();
    expect(notice.className).not.toMatch(/danger|blocking/);
  });

  it("una línia sense opció no ofereix res a revisar", async () => {
    offer = offerWithLine({ solution_option_id: null, diverged_fields: [] });
    await mount();

    expect(screen.queryByRole("button", { name: /L'opció ha canviat/ })).toBeNull();
  });

  it("una línia arxivada no ofereix revisar canvis", async () => {
    offer = offerWithLine({
      solution_option_id: "op1",
      diverged_fields: ["supplier_name"],
      archived_at: "2026-09-01T00:00:00Z",
    });
    await mount();

    expect(screen.queryByRole("button", { name: /L'opció ha canviat/ })).toBeNull();
  });

  it("el calaix ensenya un camp per divergència i cap marcat", async () => {
    offer = offerWithLine({
      solution_option_id: "op1",
      diverged_fields: ["supplier_name", "product_name"],
    });
    await mount();
    await click(/L'opció ha canviat/);

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes).toHaveLength(2);
    expect(boxes.every((box) => !box.checked)).toBe(true);
    expect(screen.getByText("Proveïdor")).toBeTruthy();
    expect(screen.getByText("Producte")).toBeTruthy();
    // Cap botó que ho adopti tot d'un cop.
    expect(screen.queryByRole("button", { name: /Sincronitza-ho tot/i })).toBeNull();
  });

  it("adopta només els camps marcats", async () => {
    offer = offerWithLine({
      solution_option_id: "op1",
      diverged_fields: ["supplier_name", "product_name", "unit_cost_ex_vat"],
    });
    await mount();
    await click(/L'opció ha canviat/);

    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    await act(async () => {
      fireEvent.click(boxes[0]);
    });
    await click("Actualitza els camps seleccionats");

    expect(post).toHaveBeenCalledWith(
      "/companies/c1/workspaces/w1/economic-offer/lines/ln1/sync-from-option",
      { expected_revision: offer!.revision, fields: ["supplier_name"] },
    );
  });

  it("sense cap camp marcat no deixa continuar", async () => {
    offer = offerWithLine({ solution_option_id: "op1", diverged_fields: ["supplier_name"] });
    await mount();
    await click(/L'opció ha canviat/);

    expect(
      screen
        .getByRole("button", { name: "Actualitza els camps seleccionats" })
        .hasAttribute("disabled"),
    ).toBe(true);
  });

  it("qui no pot veure el cost no sap ni que el cost ha canviat", async () => {
    role = "technical";
    offer = redactedOffer(
      offerWithLine({ solution_option_id: "op1", diverged_fields: ["unit_cost_ex_vat"] }),
    );
    await mount();

    // L'única divergència era interna: per a aquest rol no hi ha res a revisar.
    expect(screen.queryByRole("button", { name: /L'opció ha canviat/ })).toBeNull();
    expect(pageText()).not.toContain("Cost unitari");
  });

  it("un tècnic sí que veu una divergència pública", async () => {
    role = "technical";
    offer = redactedOffer(
      offerWithLine({
        solution_option_id: "op1",
        diverged_fields: ["supplier_name", "unit_cost_ex_vat"],
      }),
    );
    await mount();
    await click(/L'opció ha canviat/);

    expect(screen.getByText("Proveïdor")).toBeTruthy();
    expect(screen.queryByText("Cost unitari")).toBeNull();
  });

  it("el calaix no obre cap pestanya nova", async () => {
    offer = offerWithLine({ solution_option_id: "op1", diverged_fields: ["supplier_name"] });
    await mount();
    await click(/L'opció ha canviat/);

    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });
});
