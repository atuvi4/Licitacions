import { describe, expect, it } from "vitest";

import { normaliseText, searchMatcher } from "./text";

describe("cerca de text", () => {
  it("normalitza accents, majúscules i espais", () => {
    expect(normaliseText("  Telefonia MÒBIL ")).toBe("telefonia mobil");
  });

  it("troba dins de qualsevol dels textos, sense accents, i sense consulta tot passa", () => {
    const matches = searchMatcher("mobil");
    expect(matches("Telefonia mòbil")).toBe(true);
    expect(matches(null, undefined, "Centraleta", "Operador majorista · Telefonia mòbil")).toBe(true);
    expect(matches("Centraleta i veu IP")).toBe(false);
    expect(searchMatcher("   ")("qualsevol cosa")).toBe(true);
    expect(searchMatcher("")(null)).toBe(true);
  });
});
