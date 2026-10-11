import { describe, expect, it } from "vitest";

import { cpvFilterPrefix, cpvMatch, cpvMatchText } from "./cpvMatch";

const cpvs = [
  { cpv_code: "45314320", is_main: true, description: "Instal·lació de cables" },
  { cpv_code: "32000000", is_main: false, description: "Equip de ràdio" },
  { cpv_code: "72000000", is_main: false, description: null },
];

describe("cpvFilterPrefix", () => {
  it("només dígits, com a molt els 8 del codi (sense el de control), com el servei", () => {
    expect(cpvFilterPrefix("45314320-6")).toBe("45314320");
    expect(cpvFilterPrefix(" 4531 ")).toBe("4531");
    expect(cpvFilterPrefix("fibra")).toBeNull();
    expect(cpvFilterPrefix("")).toBeNull();
    expect(cpvFilterPrefix(null)).toBeNull();
  });
});

describe("cpvMatch (C4): quin CPV de la fila ha fet coincidir el filtre", () => {
  it("sense filtre no diu res", () => {
    expect(cpvMatch(cpvs, "")).toBeNull();
    expect(cpvMatch(cpvs, null)).toBeNull();
  });

  it("el principal coincideix: és el que es veu, i prou", () => {
    expect(cpvMatch(cpvs, "4531")).toEqual({ kind: "main", cpv: cpvs[0] });
  });

  it("només un secundari coincideix: la fila entra per un CPV que no es veu", () => {
    expect(cpvMatch(cpvs, "32")).toEqual({ kind: "secondary", cpv: cpvs[1] });
    expect(cpvMatch(cpvs, "72000000")).toEqual({ kind: "secondary", cpv: cpvs[2] });
  });

  it("cap CPV de la fila coincideix: el servei només pot haver-la inclòs per un lot", () => {
    expect(cpvMatch(cpvs, "50")).toEqual({ kind: "lot_only", cpv: null });
    expect(cpvMatch([], "50")).toEqual({ kind: "lot_only", cpv: null });
  });

  it("el text diu quin CPV ha coincidit, amb la descripció només si en té", () => {
    expect(cpvMatchText({ kind: "secondary", cpv: cpvs[1] })).toBe("Coincideix amb el filtre pel CPV secundari 32000000 · Equip de ràdio");
    expect(cpvMatchText({ kind: "secondary", cpv: cpvs[2] })).toBe("Coincideix amb el filtre pel CPV secundari 72000000");
    expect(cpvMatchText({ kind: "lot_only", cpv: null })).toBe("Coincideix amb el filtre per un CPV d'un lot");
    expect(cpvMatchText({ kind: "main", cpv: cpvs[0] })).toBe("Coincideix amb el filtre");
  });
});
