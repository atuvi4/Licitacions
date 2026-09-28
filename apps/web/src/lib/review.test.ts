import { describe, expect, it } from "vitest";

import { awardAll, awardScoring, reviewRequirements } from "@/test/review-fixture";

import {
  NO_FILTERS,
  checkItemNames,
  checkTone,
  filterOptions,
  filterRequirements,
  formatPoints,
  formatValue,
  scoringBlocks,
  shortDocument,
  structureSections,
  unitLabel,
  whereLine,
} from "./review";

describe("format de la revisió", () => {
  it("escriu els valors sense claus internes i amb el símbol de l'operador", () => {
    const [m15] = reviewRequirements;
    expect(formatValue(m15.values[0])).toBe("≥ 64 trucades");
    expect(formatValue({ ...m15.values[0], operator: "gte", value: 450000, unit_label: "EUR" })).toBe("≥ 450.000 EUR");
    expect(formatValue({ ...m15.values[0], operator: "exists", value: true, unit_label: null })).toBe("Sí");
  });

  it("tradueix les unitats tancades de l'extracció i deixa les del revisor", () => {
    const base = reviewRequirements[0].values[0];
    expect(formatValue({ ...base, operator: "lte", value: 10, unit_label: "percent" })).toBe("≤ 10 %");
    expect(formatValue({ ...base, operator: null, value: 8, unit_label: "hours" })).toBe("8 hores");
    expect(formatValue({ ...base, operator: null, value: 1, unit_label: "days" })).toBe("1 dia");
    expect(formatValue({ ...base, operator: "gte", value: 600, unit_label: "mbps" })).toBe("≥ 600 Mbps");
    expect(formatValue({ ...base, operator: ">=", value: 720, unit_label: "p" })).toBe("≥ 720 p");
    expect(unitLabel("usuaris", 200)).toBe("usuaris");
  });

  it("escurça el document i diu la pàgina", () => {
    expect(shortDocument("PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf")).toBe("PPT");
    expect(shortDocument("Informe necessitats contractació.pdf")).toBe("Informe necessitats contractació");
    expect(whereLine(["PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf"], [23])).toBe("PPT · p. 23");
    expect(whereLine([], [])).toBeNull();
  });

  it("singular i plural de punts", () => {
    expect(formatPoints(1)).toBe("1 punt");
    expect(formatPoints(80)).toBe("80 punts");
    expect(formatPoints(null)).toBe("Sense punts");
  });
});

describe("filtres de requisits", () => {
  it("filtra per estat, origen, obligació, categoria i text sense accents", () => {
    const only = (filters: Partial<typeof NO_FILTERS>) =>
      filterRequirements(reviewRequirements, { ...NO_FILTERS, ...filters }).map((item) => item.canonical_id);

    expect(only({ verification: "needs_review" })).toEqual(["r-recording"]);
    expect(only({ provenance: "manual" })).toEqual(["m15", "m06", "m21"]);
    expect(only({ obligation: "Condicional" })).toEqual(["r-solvency"]);
    expect(only({ category: "economic_solvency" })).toEqual(["r-solvency"]);
    expect(only({ text: "simultanies" })).toEqual(["m15"]);
    expect(only({ text: "64 trucades" })).toEqual(["m15"]);
    expect(only({ provenance: "manual", text: "ddi" })).toEqual(["m21"]);
  });

  it("les opcions surten de les dades, amb etiqueta i recompte", () => {
    const options = filterOptions(reviewRequirements);
    expect(options.provenance).toEqual([
      { value: "manual", label: "Afegit manualment", count: 3 },
      { value: "automated", label: "Extret automàticament", count: 5 },
    ]);
    expect(options.verification.map((option) => option.label)).toEqual(["Cal revisar", "Pendent de revisar"]);
  });
});

describe("criteris d'adjudicació", () => {
  it("la vista de puntuació agrupa els 8 criteris sota el bloc de 20 punts", () => {
    const blocks = scoringBlocks(awardScoring);
    expect(blocks.map((block) => block.kind)).toEqual(["single", "group"]);
    const [price, group] = blocks;
    expect(price.kind === "single" && price.row.points).toBe(80);
    if (group.kind !== "group") throw new Error("s'esperava un bloc");
    expect(group.parent.points).toBe(20);
    expect(group.rows.map((row) => row.points)).toEqual([5, 4, 3, 2, 2, 2, 1, 1]);
    expect(group.rows.reduce((sum, row) => sum + (row.points ?? 0), 0)).toBe(20);
  });

  it("l'estructura completa separa el que compta del que no", () => {
    const sections = structureSections(awardAll);
    expect(sections.map((section) => [section.key, section.rows.length])).toEqual([
      ["counts", 9],
      ["parent_total", 1],
      ["restatement", 3],
      ["offer_form_view", 1],
      ["not_scored", 7],
      ["desirable", 2],
    ]);
    const counting = sections.filter((section) => section.counts).flatMap((section) => section.rows);
    expect(counting).toHaveLength(9);
    // El preu primer i després de més a menys, com a la vista de puntuació.
    expect(counting.map((row) => row.points)).toEqual([80, 5, 4, 3, 2, 2, 2, 1, 1]);
    expect(sections.reduce((sum, section) => sum + section.rows.length, 0)).toBe(23);
  });

  it("els avisos informatius no semblen errors i porten els noms", () => {
    const [formula, restatement] = awardScoring.checks;
    expect(checkTone(formula)).toBe("info");
    expect(checkTone(restatement)).toBe("info");
    expect(checkTone({ ...formula, code: "total_points_mismatch" })).toBe("attention");
    expect(checkItemNames(awardScoring, formula)).toEqual(["Criteri preu"]);
    expect(checkItemNames(awardScoring, restatement)).toEqual([
      "Servei de trànsit de veu de mòbils - Minuts mensuals per mòbil 20000 minuts",
      "Suport - Comptar amb centre de suport i atenció en català",
    ]);
  });
});
