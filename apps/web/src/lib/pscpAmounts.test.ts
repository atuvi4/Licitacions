import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";
import {
  PLACSP_NO_LOT_AMOUNTS_TEXT,
  PLACSP_SOME_LOT_AMOUNTS_TEXT,
  PSCP_INCOHERENT_TEXT,
  PSCP_ZERO_TEXT,
  budgetWithTaxView,
  failedChecksText,
  isSumOnlyConflict,
  isShownObservation,
  lotAmountCell,
  pscpAttribution,
  pscpConflictNotice,
  pscpLotConflictReason,
  pscpSourceLine,
} from "@/lib/pscpAmounts";
import type { AmountObservation, TenderLot } from "@/lib/tenders";

const nbsp = (text: string | null) => text?.replace(/ /g, " ") ?? null;

function obs(overrides: Partial<AmountObservation> = {}): AmountObservation {
  return {
    status: "accepted",
    amount: "247121.63",
    currency: "EUR",
    published_zero: false,
    failed_checks: [],
    publication_url: "https://contractaciopublica.cat/ca/detall-publicacio/x/1",
    source_updated_at: "2026-10-16T06:30:00Z",
    retrieved_at: "2026-10-16T12:00:00Z",
    source_code: "pscp_open_data:ybgg-dgi6",
    ...overrides,
  };
}

const lot = (id: string, base_budget: string | null = null) => ({ id, base_budget }) as TenderLot;
const OFFICIAL =
  "Font: Generalitat de Catalunya. Departament d'Economia i Finances — Contractació pública a Catalunya: publicacions a la Plataforma de serveis de contractació pública (dades obertes). Llicència oberta d'ús d'informació – Catalunya.";

describe("PBL amb IVA de la PSCP (F-06a)", () => {
  it("les comprovacions, en català i amb «i» al final", () => {
    expect(failedChecksText([])).toBe("");
    expect(failedChecksText(["vec"])).toBe("VEC");
    expect(failedChecksText(["organ", "pbl_without_tax", "vec", "lots"])).toBe("òrgan, PBL sense IVA, VEC i lots");
  });

  it("la línia de font porta la data (al dia) si n'hi ha", () => {
    expect(pscpSourceLine("2026-10-16T06:30:00Z")).toBe(
      `Segons la PSCP (dades obertes de la Generalitat de Catalunya), actualitzat el ${formatDate("2026-10-16T06:30:00Z")}`,
    );
    expect(pscpSourceLine(null)).toBe("Segons la PSCP (dades obertes de la Generalitat de Catalunya)");
  });

  it("l'avís de conflicte: amb import, amb i sense comprovacions, i sense import", () => {
    expect(nbsp(pscpConflictNotice(obs({ status: "conflict", amount: "98600.00", failed_checks: ["organ", "vec"] })))).toBe(
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP (òrgan i VEC). No es fa servir fins que es verifiqui.",
    );
    expect(nbsp(pscpConflictNotice(obs({ status: "conflict", amount: "98600.00" })))).toBe(
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no ha superat les comprovacions de coherència de la font. No es fa servir fins que es verifiqui.",
    );
    expect(pscpConflictNotice(obs({ status: "conflict", amount: null }))).toBe(
      "La PSCP no hi publica cap import vàlid com a pressupost base amb IVA. No es fa servir fins que es verifiqui.",
    );
    expect(pscpConflictNotice(obs({ status: "conflict", amount: null, failed_checks: ["lots"] }))).toBe(
      "La PSCP no hi publica cap import vàlid com a pressupost base amb IVA, i les seves dades no coincideixen amb les de la PLACSP (lots). No es fa servir fins que es verifiqui.",
    );
  });

  it("el text del no_value a 0 és l'exacte de la proposta", () => {
    expect(PSCP_ZERO_TEXT).toBe("Publicat a 0,00 € a la PSCP (sense pressupost base de despesa; no s'utilitza com a import econòmic)");
  });

  it("què es mostra: un no_value nul no; els altres estats sí", () => {
    expect(isShownObservation(obs())).toBe(true);
    expect(isShownObservation(obs({ status: "conflict", amount: null }))).toBe(true);
    expect(isShownObservation(obs({ status: "no_value", amount: "0", published_zero: true }))).toBe(true);
    expect(isShownObservation(obs({ status: "no_value", amount: null }))).toBe(false);
    expect(isShownObservation(null)).toBe(false);
  });

  it("la vista: si la PLACSP ja publica el PBL amb IVA, el fet és el seu; l'atribució, només si es mostra alguna cosa", () => {
    const observations = { expedient: obs(), lots: {}, attribution: OFFICIAL, source_updated_at: "2026-10-16T06:30:00Z" };
    expect(budgetWithTaxView({ base_budget: null, lots: [], budget_with_tax_observations: observations }).expedient).toEqual(obs());
    const placsp = budgetWithTaxView({ base_budget: "200000", lots: [], budget_with_tax_observations: observations });
    expect(placsp.expedient).toBeNull();
    expect(placsp.attribution).toBeNull();
    expect(budgetWithTaxView({ base_budget: null, lots: [] }).attribution).toBeNull();
    expect(
      budgetWithTaxView({ base_budget: null, lots: [], budget_with_tax_observations: { ...observations, expedient: obs({ status: "no_value", amount: null }) } }).attribution,
    ).toBeNull();
  });

  it("lots: només els de la fitxa, i la data de l'atribució és la més recent de les mostrades", () => {
    const view = budgetWithTaxView({
      base_budget: null,
      lots: [lot("l1"), lot("l2")],
      budget_with_tax_observations: {
        expedient: null,
        lots: {
          l1: obs({ source_updated_at: "2026-10-10T00:00:00Z" }),
          l2: obs({ status: "conflict", source_updated_at: "2026-10-16T06:30:00Z" }),
          gone: obs(),
        },
        attribution: OFFICIAL,
        source_updated_at: null,
      },
    });
    expect(Object.keys(view.lots)).toEqual(["l1", "l2"]);
    expect(view.attribution).toBe(pscpAttribution(OFFICIAL, "2026-10-16T06:30:00Z"));
    // El text és el de l'API, tal qual, i la data a continuació.
    expect(view.attribution).toBe(`${OFFICIAL} Darrera actualització: ${formatDate("2026-10-16T06:30:00Z")}.`);
  });

  it("un lot amb PBL amb IVA de la PLACSP el conserva: la PSCP no s'hi mostra com a segon PBL", () => {
    const view = budgetWithTaxView({
      base_budget: null,
      lots: [lot("l1", "1000.00"), lot("l2")],
      budget_with_tax_observations: { expedient: null, lots: { l1: obs(), l2: obs() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(Object.keys(view.lots)).toEqual(["l2"]);
    const onlyPlacsp = budgetWithTaxView({
      base_budget: null,
      lots: [lot("l1", "1000.00")],
      budget_with_tax_observations: { expedient: null, lots: { l1: obs() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(onlyPlacsp.lots).toEqual({});
    expect(onlyPlacsp.attribution).toBeNull();
  });

  it("zeros (19-10-2026): un PBL amb IVA de la PLACSP a 0,00 € no és un import i no amaga la PSCP", () => {
    const view = budgetWithTaxView({
      base_budget: "0.00",
      lots: [lot("l1", "0.00"), lot("l2", "1000.00")],
      budget_with_tax_observations: { expedient: obs(), lots: { l1: obs(), l2: obs() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(view.expedient).toEqual(obs());
    expect(Object.keys(view.lots)).toEqual(["l1"]);
    expect(view.attribution).not.toBeNull();
  });

  it("sense text d'atribució de l'API no se n'inventa cap; sense data, «desconeguda»", () => {
    const observations = { expedient: obs({ source_updated_at: null }), lots: {}, attribution: null, source_updated_at: null };
    expect(budgetWithTaxView({ base_budget: null, lots: [], budget_with_tax_observations: observations }).attribution).toBeNull();
    expect(pscpAttribution(OFFICIAL, null)).toBe(`${OFFICIAL} Darrera actualització: desconeguda.`);
  });
  it("lot_sum: la suma dels lots de la PSCP contra el seu total; mai no culpa la PLACSP", () => {
    expect(pscpConflictNotice(obs({ status: "conflict", amount: "98600.00", failed_checks: ["lot_sum"] }))).toBe(
      "La suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP. No es fa servir fins que es verifiqui.",
    );
    expect(pscpConflictNotice(obs({ status: "conflict", amount: "98600.00", failed_checks: ["lot_sum"] }))).not.toContain("PLACSP");
    // Amb una discrepància amb la PLACSP també, totes dues coses, i `lot_sum` no surt al parèntesi.
    expect(nbsp(pscpConflictNotice(obs({ status: "conflict", amount: "98600.00", failed_checks: ["lots", "lot_sum"] })))).toBe(
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP (lots). A més, la suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP. No es fa servir fins que es verifiqui.",
    );
    expect(failedChecksText(["vec", "lot_sum"])).toBe("VEC");
  });

  it("evaluation_error: no s'ha pogut verificar, mai «no hi publica cap import vàlid»", () => {
    const notice = pscpConflictNotice(obs({ status: "conflict", amount: null, evaluation_error: true }));
    expect(notice).toBe("No s'ha pogut verificar l'import publicat a la PSCP. No es fa servir fins que es verifiqui.");
    expect(notice).not.toContain("no hi publica");
  });
});

describe("F-06e: imports de lot de la PSCP a la taula «Lots»", () => {
  const net = (overrides: Partial<AmountObservation> = {}) => obs({ amount_kind: "pbl_without_tax", amount: "36190.00", ...overrides });
  const vec = (overrides: Partial<AmountObservation> = {}) => obs({ amount_kind: "estimated_value", amount: "72380.00", ...overrides });
  const fullLot = (id: string, values: Partial<TenderLot> = {}) =>
    ({ id, base_budget: null, base_budget_without_tax: null, estimated_value: null, ...values }) as TenderLot;

  it("la cel·la: la PLACSP útil mana; si no, la PSCP que es mostra; un 0,00 € de la PLACSP no és cap import", () => {
    expect(lotAmountCell("1000.00", net())).toEqual({ source: "placsp", value: "1000.00" });
    expect(lotAmountCell("0.00", net())).toEqual({ source: "pscp", observation: net() });
    expect(lotAmountCell(null, net({ status: "conflict" }))).toEqual({ source: "pscp", observation: net({ status: "conflict" }) });
    expect(lotAmountCell(null, net({ status: "no_value", amount: "0.00", published_zero: true })).source).toBe("pscp");
    expect(lotAmountCell(null, net({ status: "no_value", amount: null }))).toEqual({ source: "none" });
    expect(lotAmountCell(null, undefined)).toEqual({ source: "none" });
  });

  it("el motiu d'un conflicte de lot: la PLACSP només si hi ha discrepància amb la PLACSP", () => {
    expect(pscpLotConflictReason(net({ status: "conflict", failed_checks: ["lot_sum"] }))).toBe(
      "La suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP.",
    );
    expect(pscpLotConflictReason(net({ status: "conflict", failed_checks: ["organ", "lot_sum"] }))).toBe(
      "No coincideix amb les dades de la PLACSP (òrgan). La suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP.",
    );
    expect(pscpLotConflictReason(vec({ status: "conflict", failed_checks: [] }))).toBe(PSCP_INCOHERENT_TEXT);
    expect(pscpLotConflictReason(vec({ status: "conflict", evaluation_error: true }))).toBe("No s'ha pogut verificar l'import publicat a la PSCP.");
  });

  it("les tres cel·les de cada lot, una sola indicació per a la taula i l'atribució amb només imports nous", () => {
    const view = budgetWithTaxView({
      base_budget: null,
      lots: [fullLot("l1"), fullLot("l2")],
      budget_with_tax_observations: {
        expedient: null,
        lots: {},
        lots_pbl_without_tax: { l1: net() },
        lots_estimated_value: { l1: vec({ source_updated_at: "2026-10-19T08:00:20Z" }), l2: vec({ status: "no_value", amount: null }) },
        attribution: OFFICIAL,
        source_updated_at: null,
      },
    });
    expect(view.lotCells.l1).toEqual({
      estimated_value: { source: "pscp", observation: vec({ source_updated_at: "2026-10-19T08:00:20Z" }) },
      base_budget_without_tax: { source: "pscp", observation: net() },
      base_budget: { source: "none" },
    });
    expect(view.lotCells.l2).toEqual({ estimated_value: { source: "none" }, base_budget_without_tax: { source: "none" }, base_budget: { source: "none" } });
    expect(view.lotsNote).toBe(PLACSP_NO_LOT_AMOUNTS_TEXT);
    expect(view.lots).toEqual({});
    expect(view.attribution).toBe(`${OFFICIAL} Darrera actualització: ${formatDate("2026-10-19T08:00:20Z")}.`);
  });

  it("amb algun import de la PLACSP, la indicació ho diu; tot de la PLACSP, cap indicació ni atribució", () => {
    const mixed = budgetWithTaxView({
      base_budget: null,
      lots: [fullLot("l1", { estimated_value: "50000.00" }), fullLot("l2")],
      budget_with_tax_observations: { expedient: null, lots: {}, lots_estimated_value: { l2: vec() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(mixed.lotsNote).toBe(PLACSP_SOME_LOT_AMOUNTS_TEXT);
    const placsp = budgetWithTaxView({
      base_budget: null,
      lots: [fullLot("l1", { estimated_value: "50000.00" })],
      budget_with_tax_observations: { expedient: null, lots: {}, lots_estimated_value: { l1: vec() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(placsp.lotCells.l1.estimated_value).toEqual({ source: "placsp", value: "50000.00" });
    expect(placsp.lotsNote).toBeNull();
    expect(placsp.attribution).toBeNull();
  });

  it("conflicte de suma: un per columna, amb el primer enllaç; cap si no n'hi ha", () => {
    const sum = (url: string | null) => net({ status: "conflict", failed_checks: ["lot_sum"], publication_url: url });
    const view = budgetWithTaxView({
      base_budget: null,
      lots: [fullLot("l1"), fullLot("l2")],
      budget_with_tax_observations: {
        expedient: null,
        lots: {},
        lots_pbl_without_tax: { l1: sum(null), l2: sum("https://contractaciopublica.cat/ca/detall-publicacio/x/2") },
        lots_estimated_value: { l1: vec({ status: "conflict", failed_checks: ["vec"] }) },
        attribution: OFFICIAL,
        source_updated_at: null,
      },
    });
    expect(view.lotsSumConflicts).toEqual([
      {
        label: "PBL sense IVA",
        text: "PBL sense IVA: la suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP.",
        publication_url: "https://contractaciopublica.cat/ca/detall-publicacio/x/2",
      },
    ]);
    expect(isSumOnlyConflict(sum(null))).toBe(true);
    expect(isSumOnlyConflict(net({ status: "conflict", failed_checks: ["lots", "lot_sum"] }))).toBe(false);
    expect(isSumOnlyConflict(net({ status: "conflict", failed_checks: ["lot_sum"], evaluation_error: true }))).toBe(false);
    expect(view.lotsWithoutAmounts).toBe(false);
  });

  it("sense cap import útil ni cap observació a cap lot, la taula és la d'abans (`lotsWithoutAmounts`)", () => {
    const empty = budgetWithTaxView({ base_budget: null, lots: [fullLot("l1", { estimated_value: "0.00" })], budget_with_tax_observations: null });
    expect(empty.lotsWithoutAmounts).toBe(true);
    expect(empty.lotsNote).toBeNull();
    const withPlacsp = budgetWithTaxView({ base_budget: null, lots: [fullLot("l1", { estimated_value: "100.00" })] });
    expect(withPlacsp.lotsWithoutAmounts).toBe(false);
    const withObservation = budgetWithTaxView({
      base_budget: null,
      lots: [fullLot("l1")],
      budget_with_tax_observations: { expedient: null, lots: {}, lots_estimated_value: { l1: vec({ status: "no_value", amount: null }) }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(withObservation.lotsWithoutAmounts).toBe(false);
    expect(budgetWithTaxView({ base_budget: null, lots: [] }).lotsWithoutAmounts).toBe(false);
  });

  it("una resposta d'abans de F-06e (sense els camps nous) continua funcionant", () => {
    const view = budgetWithTaxView({
      base_budget: null,
      lots: [lot("l1")],
      budget_with_tax_observations: { expedient: null, lots: { l1: obs() }, attribution: OFFICIAL, source_updated_at: null },
    });
    expect(view.lotCells.l1.base_budget).toEqual({ source: "pscp", observation: obs() });
    expect(view.lotCells.l1.estimated_value).toEqual({ source: "none" });
    expect(view.lotsNote).toBe(PLACSP_NO_LOT_AMOUNTS_TEXT);
  });
});
