import { describe, expect, it } from "vitest";

import { amountText, checkAnnualAmounts, dayOrNull, errorText, parseAmount, parseCount, type AnnualRow } from "./forms";

const row = (key: string, year: string, amount: string): AnnualRow => ({ key, year, amount });
const project = { startDate: "2024-03-01", endDate: "2026-06-30", total: "100000" };

describe("parseAmount", () => {
  it("llegeix el format europeu i el deixa com el vol l'API", () => {
    expect(parseAmount("1.234,56")).toBe("1234.56");
    expect(parseAmount("12.500.000")).toBe("12500000");
    expect(parseAmount("1234,5")).toBe("1234.5");
    expect(parseAmount("1234.5")).toBe("1234.5");
    expect(parseAmount(" 150 000 € ")).toBe("150000");
    expect(parseAmount("0")).toBe("0");
  });

  it("buit és null; negatiu, text o més de dos decimals no valen", () => {
    expect(parseAmount("")).toBeNull();
    expect(parseAmount("  ")).toBeNull();
    expect(parseAmount("-5")).toBeUndefined();
    expect(parseAmount("abc")).toBeUndefined();
    expect(parseAmount("1,234")).toBeUndefined();
    expect(parseAmount("1.2.3,4")).toBeUndefined();
  });

  it("amountText torna l'import de l'API al camp, amb coma", () => {
    expect(amountText("1234.50")).toBe("1234,5");
    expect(amountText("90000.00")).toBe("90000");
    expect(amountText(null)).toBe("");
    expect(parseAmount(amountText("1234.56"))).toBe("1234.56");
  });
});

describe("parseCount i dayOrNull", () => {
  it("un recompte és un enter no negatiu", () => {
    expect(parseCount("256")).toBe(256);
    expect(parseCount("0")).toBe(0);
    expect(parseCount("1.024")).toBe(1024);
    expect(parseCount("")).toBeNull();
    expect(parseCount("-1")).toBeUndefined();
    expect(parseCount("2,5")).toBeUndefined();
  });

  it("una data en blanc és null, mai avui", () => {
    expect(dayOrNull("")).toBeNull();
    expect(dayOrNull("2026-03-31")).toBe("2026-03-31");
  });
});

describe("checkAnnualAmounts (les regles de l'API)", () => {
  it("accepta files bones i les ordena per any", () => {
    const check = checkAnnualAmounts([row("a", "2025", "20.000"), row("b", "2024", "30000,50")], project);
    expect(check.rows).toEqual({});
    expect(check.value).toEqual([
      { year: 2024, amount: "30000.50" },
      { year: 2025, amount: "20000" },
    ]);
  });

  it("refusa anys repetits", () => {
    const check = checkAnnualAmounts([row("a", "2024", "1"), row("b", "2024", "2")], project);
    expect(check.rows.b?.year).toMatch(/ja hi és/);
    expect(check.value).toBeUndefined();
  });

  it("refusa anys fora de les dates del projecte", () => {
    const check = checkAnnualAmounts([row("a", "2023", "1"), row("b", "2027", "1")], project);
    expect(check.rows.a?.year).toMatch(/fora de les dates/);
    expect(check.rows.b?.year).toMatch(/fora de les dates/);
  });

  it("sense dates, qualsevol any de quatre xifres val", () => {
    const check = checkAnnualAmounts([row("a", "2019", "1")], { startDate: null, endDate: null, total: null });
    expect(check.value).toEqual([{ year: 2019, amount: "1" }]);
  });

  it("refusa imports negatius, buits o mal escrits i anys incomplets", () => {
    const check = checkAnnualAmounts([row("a", "2024", "-10"), row("b", "2025", ""), row("c", "25", "1")], project);
    expect(check.rows.a?.amount).toMatch(/no vàlid/);
    expect(check.rows.b?.amount).toMatch(/Indica/);
    expect(check.rows.c?.year).toMatch(/quatre xifres/);
  });

  it("la suma no pot passar de l'import total; igual sí", () => {
    expect(checkAnnualAmounts([row("a", "2024", "60000"), row("b", "2025", "40000,01")], project).total).toMatch(/sumen més/);
    expect(checkAnnualAmounts([row("a", "2024", "60000"), row("b", "2025", "40000")], project).value).toHaveLength(2);
    // Sense import total, no hi ha sostre.
    expect(checkAnnualAmounts([row("a", "2024", "999999")], { ...project, total: null }).value).toHaveLength(1);
  });

  it("cap fila és un desglossament buit (esborra el que hi havia)", () => {
    expect(checkAnnualAmounts([], project).value).toEqual([]);
  });
});

describe("errorText", () => {
  it("diu el missatge i les regles del conjunt, sense prefixos tècnics ni JSON", () => {
    const text = errorText("Les dades enviades no son valides.", {
      "": "Value error, La data de fi no pot ser anterior a la d'inici.",
      name: "String should have at least 2 characters",
      weird: '{"loc": ["body"]}',
    }, ["name"]);
    expect(text).toBe("Les dades enviades no son valides. La data de fi no pot ser anterior a la d'inici.");
    expect(errorText(null, {})).toBeNull();
  });
});
