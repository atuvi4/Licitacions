import { describe, expect, it } from "vitest";

import { describeChange, hidesSubject, publicationStatusLabel, type TenderChangeEvent } from "./tender-changes";

function event(overrides: Partial<TenderChangeEvent>): TenderChangeEvent {
  return {
    change_code: "TITLE_CHANGED",
    subject: null,
    old_value: null,
    new_value: null,
    impacts: ["informational"],
    is_material: false,
    detected_at: "2026-10-06T10:00:00Z",
    ...overrides,
  };
}

describe("describeChange", () => {
  it("presència a la font: només la frase, mai true → false ni el camp cru", () => {
    const removed = event({ change_code: "SOURCE_ENTRY_REMOVED", subject: "source_removed", old_value: false, new_value: true });
    const restored = event({ change_code: "SOURCE_ENTRY_RESTORED", subject: "source_removed", old_value: true, new_value: false });
    expect(describeChange(removed)).toBe("Ja no disponible a la font");
    expect(describeChange(restored)).toBe("Torna a estar disponible a la font");
    expect(hidesSubject(removed)).toBe(true);
  });

  it("documents afegits o retirats: el nom, mai la URL; «Document» sense nom", () => {
    const url = "https://contrataciondelestado.es/wps/download/abc";
    expect(describeChange(event({ change_code: "DOCUMENT_REMOVED", subject: url, old_value: "Plec.pdf" }))).toBe("Document retirat: Plec.pdf");
    expect(describeChange(event({ change_code: "DOCUMENT_ADDED", subject: url, new_value: "Annex.pdf" }))).toBe("Document nou: Annex.pdf");
    expect(describeChange(event({ change_code: "DOCUMENT_REMOVED", subject: url }))).toBe("Document retirat: Document");
    expect(describeChange(event({ change_code: "DOCUMENT_REMOVED", subject: url, old_value: "Plec.pdf" }))).not.toContain("http");
    expect(hidesSubject(event({ change_code: "DOCUMENT_REMOVED", subject: url }))).toBe(true);
  });

  it("la resta de canvis continua mostrant abans → després", () => {
    expect(describeChange(event({ old_value: "A", new_value: "B" }))).toBe("Títol modificat: A → B");
  });
});

describe("publicationStatusLabel", () => {
  it("distingeix conflicte, error i publicació anterior", () => {
    expect(publicationStatusLabel("processed")).toBe("Processada");
    expect(publicationStatusLabel("failed")).toBe("Amb error");
    expect(publicationStatusLabel("conflict")).toBe("En conflicte (mateix instant, contingut diferent)");
    expect(publicationStatusLabel("superseded")).toBe("Publicació anterior (no aplicada)");
    expect(publicationStatusLabel("pending")).toBe("Pendent");
  });
});
