import { describe, expect, it } from "vitest";

import { formatDate } from "@/lib/format";
import {
  PSCP_ZERO_TEXT,
  budgetWithTaxView,
  failedChecksText,
  isShownObservation,
  pscpAttribution,
  pscpConflictNotice,
  pscpLotLine,
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

const lot = (id: string) => ({ id }) as TenderLot;

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
      "La PSCP publica 98.600,00 € com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP. No es fa servir fins que es verifiqui.",
    );
    expect(pscpConflictNotice(obs({ status: "conflict", amount: null }))).toBe(
      "La PSCP no hi publica cap import vàlid com a pressupost base amb IVA. No es fa servir fins que es verifiqui.",
    );
    expect(pscpConflictNotice(obs({ status: "conflict", amount: null, failed_checks: ["lots"] }))).toBe(
      "La PSCP no hi publica cap import vàlid com a pressupost base amb IVA, i les seves dades no coincideixen amb les de la PLACSP (lots). No es fa servir fins que es verifiqui.",
    );
  });

  it("la línia de lot: accepted amb import, conflict pendent, no_value a 0; un no_value nul no en té", () => {
    expect(nbsp(pscpLotLine(obs({ amount: "32670.00" })))).toBe("PBL amb IVA: 32.670,00 € · PSCP");
    expect(pscpLotLine(obs({ status: "conflict", amount: "32670.00" }))).toBe("PBL amb IVA: pendent de verificació (discrepància entre fonts)");
    expect(pscpLotLine(obs({ status: "no_value", amount: "0.00", published_zero: true }))).toBe("PBL amb IVA: publicat a 0,00 € a la PSCP");
    expect(pscpLotLine(obs({ status: "no_value", amount: null, published_zero: false }))).toBeNull();
    expect(pscpLotLine(undefined)).toBeNull();
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
    const observations = { expedient: obs(), lots: {}, attribution: "x", source_updated_at: "2026-10-16T06:30:00Z" };
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
        attribution: "x",
        source_updated_at: null,
      },
    });
    expect(Object.keys(view.lots)).toEqual(["l1", "l2"]);
    expect(view.attribution).toBe(pscpAttribution("2026-10-16T06:30:00Z"));
    expect(view.attribution).toBe(
      "Font: Generalitat de Catalunya. Departament d'Economia i Finances — Contractació pública a Catalunya: publicacions a la Plataforma de serveis de contractació pública (dades obertes). Llicència oberta d'ús d'informació – Catalunya. Darrera actualització: " +
        `${formatDate("2026-10-16T06:30:00Z")}.`,
    );
  });
});
