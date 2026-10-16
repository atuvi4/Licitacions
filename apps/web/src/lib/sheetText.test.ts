import { describe, expect, it } from "vitest";

import { parsePageParam } from "@/lib/pageParam";
import { floatArtifactClean, formatCleanNumber, isSpreadsheetParser, parseSheetPage } from "@/lib/sheetText";

describe("parseSheetPage (P2-07)", () => {
  it("format v1 (el del pilot): nom del full, combinades com a nota i files amb cel·les", () => {
    const page = parseSheetPage(
      [
        "# Full: Inputs",
        "Cel·les combinades: A1:D1",
        "Fila 4 | A4: PG0 | B4: Preu màxim mensual | C4: 61 | D4: 25,00 € fixos (2.500 min)",
        "Fila 8 | A8: PL1 | B8: Bloc 1 Connectivitat | C8: 75600 | D8:",
      ].join("\n"),
    );
    expect(page.name).toBe("Inputs");
    expect(page.notes).toEqual(["Cel·les combinades: A1:D1"]);
    expect(page.rows.map((row) => row.row)).toEqual(["4", "8"]);
    expect(page.rows[0].cells.map((cell) => [cell.ref, cell.value])).toEqual([
      ["A4", "PG0"],
      ["B4", "Preu màxim mensual"],
      ["C4", "61"],
      ["D4", "25,00 € fixos (2.500 min)"],
    ]);
    expect(page.rows[1].cells[3]).toEqual({ ref: "D8", range: null, raw: "", formula: null, value: "" });
  });

  it("format v2: rang combinat de l'àncora, fórmula amb valor desat i sense valor", () => {
    const page = parseSheetPage(
      "Fila 1 | A1 [A1:D1]: Paràmetres | E1: =SUM(B2:B4) → 38326.080000000002 | F1: =A1*2 (sense valor desat)",
    );
    const [a1, e1, f1] = page.rows[0].cells;
    expect(a1).toMatchObject({ ref: "A1", range: "A1:D1", value: "Paràmetres", formula: null });
    expect(e1).toMatchObject({ ref: "E1", formula: "SUM(B2:B4)", value: "38326.080000000002" });
    expect(f1).toMatchObject({ ref: "F1", formula: "A1*2", value: null });
  });

  it("un text amb « | » dins d'una cel·la no es perd: és la continuació del valor", () => {
    const page = parseSheetPage("Fila 2 | A2: blanc | negre | B2: 3");
    expect(page.rows[0].cells.map((cell) => [cell.ref, cell.raw])).toEqual([
      ["A2", "blanc | negre"],
      ["B2", "3"],
    ]);
  });

  it("l'apòstrof d'un valor que comença per «=» no es llegeix com a fórmula", () => {
    const [cell] = parseSheetPage("Fila 3 | A3: '=no és fórmula").rows[0].cells;
    expect(cell).toMatchObject({ formula: null, value: "'=no és fórmula" });
  });

  it("una línia que no s'entén es conserva com a nota", () => {
    const page = parseSheetPage("Taula «Preus»: A1:C9 (columnes: Codi, Preu)\nlínia rara");
    expect(page.notes).toEqual(["Taula «Preus»: A1:C9 (columnes: Codi, Preu)", "línia rara"]);
    expect(page.rows).toEqual([]);
  });

  it("isSpreadsheetParser: només els parsers xlsx", () => {
    expect(isSpreadsheetParser("xlsx-v1")).toBe(true);
    expect(isSpreadsheetParser("xlsx-v2")).toBe(true);
    expect(isSpreadsheetParser("pypdf-v1")).toBe(false);
    expect(isSpreadsheetParser("docx-v1")).toBe(false);
    expect(isSpreadsheetParser(null)).toBe(false);
  });
});

describe("floatArtifactClean (P2-07): només errors evidents de coma flotant", () => {
  it("corregeix els artefactes, sense arrodonir res més", () => {
    expect(floatArtifactClean("38326.080000000002")).toBe("38326.08");
    expect(floatArtifactClean("0.30000000000000004")).toBe("0.3");
    expect(floatArtifactClean("-1.1000000000000001")).toBe("-1.1");
  });

  it("no toca enters, codis, identificadors, dates ni decimals precisos", () => {
    for (const value of ["75600", "64200000", "17130", "2026", "45678", "0.1", "1.2", "38326.08", "0.123456789012", "3.141592653589"]) {
      expect(floatArtifactClean(value)).toBeNull();
    }
  });

  it("no toca textos, notació científica ni números dins d'una frase", () => {
    for (const value of ["25,00 € fixos", "1.5E-7", "1e21", "38326.080000000002 €", "", "abc"]) {
      expect(floatArtifactClean(value)).toBeNull();
    }
  });

  it("el valor net es formata en català sense arrodonir", () => {
    expect(formatCleanNumber("38326.08")).toBe("38.326,08");
    expect(formatCleanNumber("0.3")).toBe("0,3");
  });
});

describe("parsePageParam (P2-07)", () => {
  it("només un enter ≥ 1", () => {
    expect(parsePageParam("3")).toBe(3);
    expect(parsePageParam(null)).toBeNull();
    for (const raw of ["0", "-1", "1.5", "abc", "", " 2"]) expect(parsePageParam(raw)).toBeNull();
  });
});
