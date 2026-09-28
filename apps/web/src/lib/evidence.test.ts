import { describe, expect, it } from "vitest";

import { citationPayload, sliceCodePoints, spanFromFragment, spanFromSelection, toCodePoints } from "./evidence";

/** El que faria el backend (Python): tallar per punts de codi. */
const python = (text: string, start: number, end: number) => Array.from(text).slice(start, end).join("");

describe("posicions deterministes", () => {
  it("sense caràcters especials, UTF-16 i punts de codi coincideixen", () => {
    const text = "Latència màx. <70 ms Greu";
    const span = spanFromSelection(text, 14, 20);
    expect(span).toEqual({ ok: true, charStart: 14, charEnd: 20, quote: "<70 ms" });
  });

  it("amb un caràcter fora del pla bàsic, les posicions són les de Python", () => {
    const text = "Icona 📞 Mínim 64 trucades simultànies";
    const start = text.indexOf("Mínim");
    const end = text.indexOf(" simult");
    const span = spanFromSelection(text, start, end);
    if (!span.ok) throw new Error(span.reason);
    // En UTF-16 «📞» són dues unitats; per al servidor, un sol caràcter.
    expect(span.charStart).toBe(start - 1);
    expect(python(text, span.charStart, span.charEnd)).toBe("Mínim 64 trucades");
    expect(span.quote).toBe("Mínim 64 trucades");
  });

  it("una selecció que talla un parell subrogat falla tancada", () => {
    const text = "a📞b";
    expect(toCodePoints(text, 2)).toBeUndefined();
    const span = spanFromSelection(text, 2, 4);
    expect(span.ok).toBe(false);
  });

  it("els espais dels extrems es treuen, els de dins es conserven", () => {
    const text = "Primera línia.\n  hauran de disposar de connectivitat a nivell 2 amb el datacenter\non es gestionin les trucades.  \n";
    const span = spanFromSelection(text, 14, text.length);
    if (!span.ok) throw new Error(span.reason);
    expect(span.quote.startsWith("hauran")).toBe(true);
    expect(span.quote.endsWith("trucades.")).toBe(true);
    expect(span.quote).toContain("datacenter\non es");
    expect(sliceCodePoints(text, span.charStart, span.charEnd)).toBe(span.quote);
    expect(spanFromSelection(text, 3, 3).ok).toBe(false);
  });
});

describe("fragment escrit (alternativa amb teclat)", () => {
  const page = "8.1.1.4 ... hauran de disposar de connectivitat a nivell 2 amb el datacenter\non es gestionin les trucades. Fi.";

  it("una sola aparició: posicions exactes, també si hi ha un salt de línia pel mig", () => {
    const span = spanFromFragment(page, "connectivitat a nivell 2 amb el datacenter on es gestionin les trucades");
    if (!span.ok) throw new Error(span.reason);
    expect(python(page, span.charStart, span.charEnd)).toBe(
      "connectivitat a nivell 2 amb el datacenter\non es gestionin les trucades",
    );
  });

  it("més d'una aparició o cap: no tria res", () => {
    const repeated = spanFromFragment("nivell 2 i nivell 2", "nivell 2");
    expect(repeated).toEqual({ ok: false, reason: "Aquest text hi apareix 2 vegades: allarga'l." });
    expect(spanFromFragment(page, "no hi és pas").ok).toBe(false);
    expect(spanFromFragment(page, "ab").ok).toBe(false);
  });
});

describe("càrrega", () => {
  it("només posicions i identificadors, mai el text citat", () => {
    const payload = citationPayload({
      extractionId: "x1",
      pageId: "p22",
      pageNumber: 22,
      documentName: "PPT",
      charStart: 10,
      charEnd: 40,
      quote: "text de previsualització",
    });
    expect(payload).toEqual({ kind: "extracted_page", extraction_id: "x1", page_id: "p22", char_start: 10, char_end: 40 });
    expect(JSON.stringify(payload)).not.toContain("previsualització");
  });
});
