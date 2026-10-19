import { describe, expect, it } from "vitest";

import { MANUAL_ZERO_AMOUNT_TEXT, PLACSP_ZERO_AMOUNT_TEXT, isPublishedZero, usefulAmount } from "@/lib/amounts";

describe("usefulAmount (regla única dels imports a zero)", () => {
  it("un import > 0 es retorna tal com arriba (sense reformatar ni arrodonir)", () => {
    expect(usefulAmount("204232.75")).toBe("204232.75");
    expect(usefulAmount("0.01")).toBe("0.01");
    expect(usefulAmount(1500)).toBe("1500");
  });

  it("0, 0.00, negatiu, buit, nul o no numèric: no és un import útil", () => {
    for (const value of ["0", "0.00", "-0", "-5.00", "", "  ", null, undefined, "abc", "NaN", "Infinity", 0]) {
      expect(usefulAmount(value)).toBeNull();
    }
  });
});

describe("isPublishedZero", () => {
  it("només un zero publicat, no l'absència", () => {
    expect(isPublishedZero("0.00")).toBe(true);
    expect(isPublishedZero("0")).toBe(true);
    expect(isPublishedZero(0)).toBe(true);
    expect(isPublishedZero(null)).toBe(false);
    expect(isPublishedZero(undefined)).toBe(false);
    expect(isPublishedZero("")).toBe(false);
    expect(isPublishedZero("12.00")).toBe(false);
    expect(isPublishedZero("-3")).toBe(false);
  });

  it("el text aprovat per al zero de la PLACSP, exacte", () => {
    expect(PLACSP_ZERO_AMOUNT_TEXT).toBe("Publicat a 0,00 € a la PLACSP (no s'utilitza com a import econòmic)");
    expect(MANUAL_ZERO_AMOUNT_TEXT).toContain("no s'utilitza com a import econòmic");
  });
});
