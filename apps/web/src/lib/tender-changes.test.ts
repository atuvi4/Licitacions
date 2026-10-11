import { describe, expect, it } from "vitest";

import {
  describeChange,
  hidesSubject,
  publicationStatusLabel,
  revisionOriginLabel,
  revisionsByOrigin,
  sourceVersionNumbers,
  unclassifiedRevisions,
  type TenderChangeEvent,
  type TenderHistoryRevision,
} from "./tender-changes";
import { DOCUMENT_ROLE_LABELS, ROLE_SOURCE_LABELS, TENDER_DOCUMENT_LABELS, roleSourceLabel } from "./tenders";

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

const PCAP = "https://contrataciondelestado.es/wps/download/pcap.pdf";
const ALIAS = "https://contrataciondelestado.es/wps/download/pcap-copia.pdf";
const ANNEX = "https://contractaciopublica.cat/ecofin/annex3.pdf";
const NAMES = new Map([
  [PCAP, "PCAP.pdf"],
  [ALIAS, "PCAP (còpia).pdf"],
  [ANNEX, "Annex 3.pdf"],
]);

describe("describeChange · codis del paquet documental (fix round 1, I1)", () => {
  it("estats d'aportació en català segons l'estat destí, i el document pel nom", () => {
    const code = "DOCUMENT_CONTRIBUTION_CHANGED";
    expect(describeChange(event({ change_code: code, subject: ANNEX, new_value: "pending_verification" }), NAMES)).toBe(
      "Aportació de l'operador: pendent de verificació · Annex 3.pdf",
    );
    expect(describeChange(event({ change_code: code, subject: ANNEX, old_value: "pending_verification", new_value: "analyzable" }), NAMES)).toBe(
      "Aportació de l'operador: pendent de verificació → verificat · Annex 3.pdf",
    );
    expect(describeChange(event({ change_code: code, subject: ANNEX, old_value: "pending_verification", new_value: "rejected" }), NAMES)).toBe(
      "Aportació de l'operador: pendent de verificació → rebutjat · Annex 3.pdf",
    );
    expect(describeChange(event({ change_code: code, subject: ANNEX, old_value: "rejected", new_value: "removed" }), NAMES)).toBe(
      "Aportació de l'operador: rebutjat → retirat · Annex 3.pdf",
    );
    // Sense nom conegut, cap URL; un estat que no coneixem tampoc surt cru.
    expect(describeChange(event({ change_code: code, subject: ANNEX, new_value: "verified" }))).toBe("Aportació de l'operador: verificat");
    expect(describeChange(event({ change_code: code, subject: ANNEX, new_value: "estat_nou" }))).not.toMatch(/_|https?:/);
  });

  it("àlies: el canònic pel nom (mai la URL) i «d'un altre document» sense nom", () => {
    const code = "DOCUMENT_ALIAS_CHANGED";
    expect(describeChange(event({ change_code: code, subject: ALIAS, new_value: PCAP }), NAMES)).toBe(
      "Document agrupat amb un altre amb el mateix contingut: àlies de PCAP.pdf · PCAP (còpia).pdf",
    );
    expect(describeChange(event({ change_code: code, subject: ALIAS, old_value: PCAP, new_value: null }), NAMES)).toBe(
      "Document desagrupat: deixa de ser àlies de PCAP.pdf · PCAP (còpia).pdf",
    );
    expect(describeChange(event({ change_code: code, subject: ALIAS, new_value: PCAP }))).toBe(
      "Document agrupat amb un altre amb el mateix contingut: àlies d'un altre document",
    );
  });

  it("rols revisats i tipus de la font amb els diccionaris, mai el codi", () => {
    expect(describeChange(event({ change_code: "DOCUMENT_ROLE_REVIEWED", subject: PCAP, new_value: "characteristics_table" }), NAMES)).toBe(
      "Tipus de document revisat per l'operador: quadre de característiques · PCAP.pdf",
    );
    expect(describeChange(event({ change_code: "DOCUMENT_ROLE_REVIEWED", subject: PCAP, old_value: "pcap", new_value: "inventory_annex" }), NAMES)).toBe(
      "Tipus de document revisat per l'operador: plec de clàusules administratives (PCAP) → annex d'inventari · PCAP.pdf",
    );
    expect(
      describeChange(event({ change_code: "SOURCE_TYPE_CHANGED_AFTER_REVIEW", subject: PCAP, old_value: "administrative_terms", new_value: "technical_terms" }), NAMES),
    ).toBe("La font ha canviat el tipus d'un document ja revisat (mana la revisió): Plec administratiu → Plec tècnic · PCAP.pdf");
    expect(describeChange(event({ change_code: "DOCUMENT_ROLE_REVIEWED", subject: PCAP, new_value: "rol_nou" }), NAMES)).not.toMatch(/_|https?:/);
  });

  it("adreça i metadades d'un document: cap URL ni cap clau interna; el tipus amb el diccionari", () => {
    expect(describeChange(event({ change_code: "DOCUMENT_URL_CHANGED", subject: ALIAS, old_value: PCAP, new_value: ALIAS }), NAMES)).toBe(
      "Adreça d'un document modificada · PCAP (còpia).pdf",
    );
    const metadata = describeChange(
      event({
        change_code: "DOCUMENT_METADATA_CHANGED",
        subject: PCAP,
        old_value: { name: "PCAP.pdf", document_type: "administrative_terms", published_at: null },
        new_value: { name: "PCAP definitiu.pdf", document_type: "technical_terms", published_at: null },
      }),
      NAMES,
    );
    expect(metadata).toContain("PCAP.pdf → PCAP definitiu.pdf");
    expect(metadata).toContain("Plec administratiu → Plec tècnic");
    expect(metadata).not.toMatch(/[a-z]+_[a-z_]+|https?:|[{}"]/);
    const replaced = describeChange(event({ change_code: "DOCUMENT_REPLACED", subject: PCAP, old_value: "a".repeat(64), new_value: "b".repeat(64) }), NAMES);
    expect(replaced).toBe(`Document substituït: ${"a".repeat(12)}… → ${"b".repeat(12)}… · PCAP.pdf`);
  });

  it("cap valor dels vocabularis nous surt amb un codi cru", () => {
    const samples: Array<[string, string[]]> = [
      ["DOCUMENT_CONTRIBUTION_CHANGED", ["pending_verification", "verified", "analyzable", "rejected", "removed", "not_applicable"]],
      ["DOCUMENT_ROLE_REVIEWED", Object.keys(DOCUMENT_ROLE_LABELS)],
      ["SOURCE_TYPE_CHANGED_AFTER_REVIEW", Object.keys(TENDER_DOCUMENT_LABELS)],
    ];
    for (const [code, values] of samples) {
      for (const value of values) {
        const text = describeChange(event({ change_code: code, subject: PCAP, old_value: null, new_value: value }), NAMES);
        expect(text, `${code} ${value}`).not.toMatch(/[a-z]+_[a-z_]+|https?:/);
      }
    }
    for (const source of Object.keys(ROLE_SOURCE_LABELS)) expect(roleSourceLabel(source)).not.toMatch(/_/);
  });

  it("el subject d'un canvi de document (una URL) no es mostra mai a part", () => {
    for (const code of [
      "DOCUMENT_ALIAS_CHANGED",
      "DOCUMENT_CONTRIBUTION_CHANGED",
      "DOCUMENT_ROLE_REVIEWED",
      "SOURCE_TYPE_CHANGED_AFTER_REVIEW",
      "DOCUMENT_URL_CHANGED",
      "DOCUMENT_METADATA_CHANGED",
      "DOCUMENT_REPLACED",
      "DOCUMENT_CONTENT_IDENTIFIED",
    ]) {
      expect(hidesSubject(event({ change_code: code, subject: PCAP })), code).toBe(true);
    }
    expect(hidesSubject(event({ change_code: "UN_CODI_NOU", subject: "https://exemple.cat/x" }))).toBe(true);
    expect(hidesSubject(event({ change_code: "LOT_TITLE_CHANGED", subject: "lot-2" }))).toBe(false);
  });
});

describe("origen de les revisions (M6) i numeració de versions (A3)", () => {
  const revision = (id: string, revision_number: number, origin: unknown): TenderHistoryRevision =>
    ({ id, revision_number, trigger: "ingestion", origin, created_at: "2026-10-06T10:00:00Z", change_summary: null, events: [] }) as TenderHistoryRevision;

  it("una revisió sense origen (o amb un de desconegut) va al processament intern i es compta com a no classificada", () => {
    const rows = [revision("a", 3, "source"), revision("b", 2, undefined), revision("c", 1, "nou_origen")];
    const grouped = revisionsByOrigin(rows);
    expect(grouped.source.map((r) => r.id)).toEqual(["a"]);
    expect(grouped.internal.map((r) => r.id)).toEqual(["b", "c"]);
    expect(unclassifiedRevisions(rows).map((r) => r.id)).toEqual(["b", "c"]);
    expect(revisionOriginLabel(rows[1], new Map())).toBe("Origen no informat");
  });

  it("«Versió N» només compta les revisions de la font, en ordre; les altres no en porten", () => {
    const rows = [revision("s3", 5, "source"), revision("i2", 4, "internal"), revision("s2", 3, "source"), revision("m1", 2, "manual"), revision("s1", 1, "source")];
    const versions = sourceVersionNumbers(rows);
    expect([...versions.entries()]).toEqual([
      ["s1", 1],
      ["s2", 2],
      ["s3", 3],
    ]);
    expect(revisionOriginLabel(rows[0], versions)).toBe("Versió 3");
    expect(revisionOriginLabel(rows[1], versions)).toBe("Processament intern");
    expect(revisionOriginLabel(rows[3], versions)).toBe("Editat a mà");
  });
});
