import { describe, expect, it } from "vitest";

import { cpvDescription } from "./tenders";

describe("cpvDescription (D4): el nom oficial del CPV, mai el codi repetit", () => {
  it("una descripció real es mostra", () => {
    expect(cpvDescription({ cpv_code: "45314320", description: "Instal·lació de cables" })).toBe("Instal·lació de cables");
    expect(cpvDescription({ cpv_code: "45314320", description: "  Instal·lació de cables " })).toBe("Instal·lació de cables");
  });

  it("la descripció provisional del catàleg («CPV <codi>») o el codi sol no es mostren", () => {
    expect(cpvDescription({ cpv_code: "32500000", description: "CPV 32500000" })).toBeNull();
    expect(cpvDescription({ cpv_code: "32500000", description: "cpv 32500000" })).toBeNull();
    expect(cpvDescription({ cpv_code: "32500000", description: "32500000" })).toBeNull();
    expect(cpvDescription({ cpv_code: "32500000-8", description: "32500000" })).toBeNull();
  });

  it("sense descripció, res", () => {
    expect(cpvDescription({ cpv_code: "32500000", description: null })).toBeNull();
    expect(cpvDescription({ cpv_code: "32500000", description: "" })).toBeNull();
    expect(cpvDescription({ cpv_code: "32500000" })).toBeNull();
  });

  it("un altre codi com a descripció no és el mateix codi: es mostra tal qual", () => {
    expect(cpvDescription({ cpv_code: "32500000", description: "CPV 32400000" })).toBe("CPV 32400000");
  });
});
