import { describe, expect, it } from "vitest";

import { lineFixture, lotFixture, offerFixture, sectionFixture } from "@/test/economic-fixture";
import {
  activeLots,
  formatDecimal,
  formatQuantity,
  archivedCount,
  canEditOffer,
  createPayload,
  diffLine,
  draftFromLine,
  durationOf,
  emptyDraft,
  formatAmount,
  formatPercent,
  isRevisionConflict,
  lineIsDirty,
  resolveEntity,
  sameDecimal,
  unitSuffix,
  usesDuration,
} from "@/lib/economic";

describe("Imports com a text", () => {
  it("compara decimals sense passar per coma flotant", () => {
    expect(sameDecimal("1850", "1850.0000")).toBe(true);
    expect(sameDecimal("0.10", "0.1")).toBe(true);
    expect(sameDecimal("0", "0.00")).toBe(true);
    expect(sameDecimal("1850.01", "1850.0000")).toBe(false);
    expect(sameDecimal(null, null)).toBe(true);
    expect(sameDecimal(null, "0")).toBe(false);
    // El cas que la coma flotant es menja.
    expect(sameDecimal("0.1", "0.3")).toBe(false);
    expect(sameDecimal("1000000000000000.01", "1000000000000000.02")).toBe(false);
  });

  it("formata sense inventar precisió", () => {
    expect(formatAmount("127240.00").replace(/ /g, " ")).toContain("127.240,00");
    expect(formatAmount(null)).toBe("—");
    expect(formatAmount("")).toBe("—");
    expect(formatPercent("18.0000")).toBe("18,0 %");
    // Un percentatge desconegut no és zero.
    expect(formatPercent(null)).toBe("—");
  });
});

describe("Format exacte, sense coma flotant", () => {
  it("arrodoneix els casos que la coma flotant es menja", () => {
    // `(1.005).toFixed(2)` dona "1.00" i `(2.675).toFixed(2)` dona "2.67".
    expect(formatDecimal("1.005", 2)).toBe("1,01");
    expect(formatDecimal("2.675", 2)).toBe("2,68");
    expect(formatDecimal("1850.015", 2)).toBe("1.850,02");
    expect(formatDecimal("0.005", 2)).toBe("0,01");
    expect(formatDecimal("8.165", 2)).toBe("8,17");
  });

  it("no arrodoneix cap amunt el que no toca", () => {
    expect(formatDecimal("1.004", 2)).toBe("1,00");
    expect(formatDecimal("2.674", 2)).toBe("2,67");
  });

  it("propaga la portadora fins on calgui", () => {
    expect(formatDecimal("0.999", 2)).toBe("1,00");
    expect(formatDecimal("9.999", 2)).toBe("10,00");
    expect(formatDecimal("99.995", 2)).toBe("100,00");
  });

  it("aguanta tot el rang de Numeric(18,4), on Number ja no és exacte", () => {
    expect(formatDecimal("99999999999999.9999", 2)).toBe("100.000.000.000.000,00");
    expect(formatDecimal("12345678901234.5678", 4)).toBe("12.345.678.901.234,5678");
    expect(formatAmount("99999999999999.9999")).toBe("100.000.000.000.000,00 €");
  });

  it("conserva el signe, però no inventa cap «−0»", () => {
    expect(formatDecimal("-112760.00", 2)).toBe("-112.760,00");
    expect(formatDecimal("-0.004", 2)).toBe("0,00");
    expect(formatAmount("-112760.00")).toBe("-112.760,00 €");
  });

  it("separa els milers i respecta la moneda que diu el servidor", () => {
    expect(formatAmount("127240.00")).toBe("127.240,00 €");
    expect(formatAmount("127240.00", "USD")).toBe("127.240,00 USD");
    expect(formatDecimal("999.5", 0)).toBe("1.000");
  });

  it("les quantitats no arrosseguen decimals inútils", () => {
    expect(formatQuantity("6")).toBe("6");
    expect(formatQuantity("6.0000")).toBe("6");
    expect(formatQuantity("6.5000")).toBe("6,5");
    expect(formatQuantity("1000")).toBe("1.000");
  });

  it("una resposta que no és un decimal es mostra tal qual, no com un zero", () => {
    expect(formatDecimal("no-és-un-número", 2)).toBeNull();
    expect(formatAmount("no-és-un-número")).toBe("no-és-un-número");
    expect(formatPercent("")).toBe("—");
  });
});

describe("Durada (C-3)", () => {
  it("les bases sense temps no tenen durada", () => {
    expect(usesDuration("one_off")).toBe(false);
    expect(usesDuration("per_unit")).toBe(false);
    expect(usesDuration("monthly")).toBe(true);
    expect(usesDuration("annual")).toBe(true);
    expect(durationOf(lineFixture({ pricing_basis: "one_off" })).source).toBe("not_applicable");
  });

  it("heretada del lot", () => {
    const duration = durationOf(lineFixture({ pricing_basis: "monthly", duration_months_override: null, effective_duration_months: 12 }));
    expect(duration.source).toBe("inherited");
    expect(duration.label).toBe("12 mesos");
    expect(duration.hint).toBe("Heretada del lot");
  });

  it("personalitzada", () => {
    const duration = durationOf(lineFixture({ pricing_basis: "monthly", duration_months_override: 24, effective_duration_months: 24 }));
    expect(duration.source).toBe("override");
    expect(duration.hint).toBe("Personalitzada");
  });

  it("desconeguda: ni zero ni dotze", () => {
    const duration = durationOf(lineFixture({ pricing_basis: "monthly", duration_months_override: null, effective_duration_months: null }));
    expect(duration.source).toBe("unknown");
    expect(duration.label).toBe("Durada pendent");
    expect(duration.months).toBeNull();
    expect(duration.label).not.toContain("0");
    expect(duration.label).not.toContain("12");
  });

  it("el sufix del preu surt de la base", () => {
    expect(unitSuffix("monthly")).toBe("al mes");
    expect(unitSuffix("annual")).toBe("a l'any");
    expect(unitSuffix("one_off")).toBeNull();
    expect(unitSuffix("per_unit")).toBeNull();
  });
});

describe("Arbre de l'oferta", () => {
  it("els lots arxivats no són actius", () => {
    const offer = offerFixture({
      lots: [lotFixture({ id: "a" }), lotFixture({ id: "b", archived_at: "2026-09-14T09:00:00Z" })],
    });
    expect(activeLots(offer).map((lot) => lot.id)).toEqual(["a"]);
  });

  it("compta el que hi ha arxivat a qualsevol nivell", () => {
    const offer = offerFixture({
      lots: [
        lotFixture({
          sections: [
            sectionFixture({ id: "s1", lines: [lineFixture({ id: "l1", archived_at: "2026-09-14T09:00:00Z" }), lineFixture({ id: "l2" })] }),
            sectionFixture({ id: "s2", archived_at: "2026-09-14T09:00:00Z", lines: [] }),
          ],
        }),
      ],
    });
    expect(archivedCount(offer)).toBe(2);
  });
});

describe("Enllaç profund", () => {
  const offer = offerFixture();

  it("una línia", () => {
    const target = resolveEntity(offer, "ln1");
    expect(target?.kind).toBe("line");
  });

  it("un lot", () => {
    expect(resolveEntity(offer, "lot1")?.kind).toBe("lot");
  });

  it("l'oferta sencera", () => {
    expect(resolveEntity(offer, "off1")?.kind).toBe("offer");
  });

  it("un id desconegut no força res", () => {
    expect(resolveEntity(offer, "no-hi-es")).toBeNull();
    expect(resolveEntity(offer, null)).toBeNull();
  });
});

describe("Esborrany i cos de la petició", () => {
  const line = lineFixture();

  it("l'esborrany surt de la línia, amb el cost del bloc intern", () => {
    const draft = draftFromLine(line);
    expect(draft.offered_unit_price_ex_vat).toBe("1850.0000");
    expect(draft.unit_cost_ex_vat).toBe("1520.0000");
    expect(draft.duration_months_override).toBe("");
    expect(lineIsDirty(line, draft, true)).toBe(false);
  });

  it("sense bloc intern, l'esborrany no porta cap cost", () => {
    const redacted = lineFixture({ internal_economics: null });
    expect(draftFromLine(redacted).unit_cost_ex_vat).toBe("");
  });

  it("només viatja el que ha canviat", () => {
    const draft = { ...draftFromLine(line), offered_unit_price_ex_vat: "1900" };
    expect(diffLine(line, draft, true)).toEqual({ offered_unit_price_ex_vat: "1900" });
  });

  it("reescriure el mateix número amb una altra forma no és cap canvi", () => {
    const draft = { ...draftFromLine(line), offered_unit_price_ex_vat: "1850" };
    expect(diffLine(line, draft, true)).toEqual({});
  });

  it("buidar un camp opcional envia null", () => {
    const draft = { ...draftFromLine(line), vat_rate: "  ", tender_max_unit_price_ex_vat: "" };
    expect(diffLine(line, draft, true)).toEqual({ vat_rate: null, tender_max_unit_price_ex_vat: null });
  });

  it("la durada buida vol dir heretar, no zero", () => {
    const monthly = lineFixture({ pricing_basis: "monthly", duration_months_override: 24, effective_duration_months: 24 });
    const draft = { ...draftFromLine(monthly), duration_months_override: "" };
    expect(diffLine(monthly, draft, true)).toEqual({ duration_months_override: null });
  });

  it("mai no s'envien derivats, identificadors ni res del servidor", () => {
    const draft = { ...draftFromLine(line), concept: "Un altre" };
    const payload = diffLine(line, draft, true) as Record<string, unknown>;
    for (const key of ["id", "section_id", "totals", "internal_economics", "position", "periods", "exceeds_unit_max", "effective_duration_months", "margin_on_sales_pct", "markup_on_cost_pct", "archived_at"]) {
      expect(payload).not.toHaveProperty(key);
    }
  });

  it("un rol sense costos no envia mai el cost unitari", () => {
    const draft = { ...draftFromLine(line), unit_cost_ex_vat: "999" };
    expect(diffLine(line, draft, false)).not.toHaveProperty("unit_cost_ex_vat");
    expect(diffLine(line, draft, true)).toHaveProperty("unit_cost_ex_vat", "999");
  });

  it("una línia nova porta el que el servidor exigeix i res més", () => {
    const draft = { ...emptyDraft(), concept: "Nova", quantity: "2", pricing_basis: "monthly" as const, offered_unit_price_ex_vat: "100" };
    expect(createPayload(draft, true)).toEqual({ concept: "Nova", quantity: "2", pricing_basis: "monthly", offered_unit_price_ex_vat: "100" });
  });

  it("una línia nova d'un rol sense costos tampoc no en porta", () => {
    const draft = { ...emptyDraft(), concept: "Nova", unit_cost_ex_vat: "50" };
    expect(createPayload(draft, false)).not.toHaveProperty("unit_cost_ex_vat");
  });
});

describe("Permisos i conflicte", () => {
  it("els rols que poden escriure preus són els del backend", () => {
    for (const role of ["owner", "admin", "bid_manager"]) expect(canEditOffer(role)).toBe(true);
    expect(canEditOffer("technical")).toBe(false);
    expect(canEditOffer("viewer")).toBe(false);
    expect(canEditOffer(null)).toBe(false);
  });

  it("un conflicte de revisió es reconeix pel codi, no per l'estat sol", () => {
    expect(isRevisionConflict({ status: 409, code: "economic_offer_revision_conflict" })).toBe(true);
    expect(isRevisionConflict({ status: 409, code: "economic_offer_item_archived" })).toBe(false);
    expect(isRevisionConflict({ status: 500, code: "economic_offer_revision_conflict" })).toBe(false);
    expect(isRevisionConflict(new Error("res"))).toBe(false);
    expect(isRevisionConflict(null)).toBe(false);
  });
});
