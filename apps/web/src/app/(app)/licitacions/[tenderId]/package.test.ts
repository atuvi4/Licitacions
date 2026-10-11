import { describe, expect, it } from "vitest";

import type { TenderDocumentMeta, TenderPackageGap } from "@/lib/tenders";

import {
  aliasesOf,
  contributionBadge,
  gapImpact,
  gapText,
  originBadge,
  readingCount,
  readingLine,
  removedLabel,
  topLevelDocuments,
} from "./package";

function doc(overrides: Partial<TenderDocumentMeta>): TenderDocumentMeta {
  return {
    id: "d1",
    name: "PCAP.pdf",
    document_type: "administrative_terms",
    source_document_type_code: null,
    url: "https://contrataciondelestado.es/d1",
    mime_type: "application/pdf",
    published_at: null,
    removed_at: null,
    latest_file: null,
    origin: "feed",
    contribution_status: "not_applicable",
    duplicate_of_document_id: null,
    analyzable: true,
    ...overrides,
  };
}

const extracted = {
  file_id: "f",
  version_number: 1,
  download_status: "downloaded",
  origin: "fetched",
  file_size: 1,
  sha256: "a",
  mime_type: "application/pdf",
  text_extraction_supported: true,
  analysis_status: "extracted",
  extraction_status: "extracted",
  quality: "good",
  page_count: 3,
  text_char_count: 100,
  ocr_candidate: false,
  extraction_id: "e",
  processed_at: null,
  error_code: null,
} as const satisfies TenderDocumentMeta["latest_file"];

describe("badges d'origen i de contribució", () => {
  it("diu d'on surt cada document i, si és una aportació, en quin estat és", () => {
    expect(originBadge(doc({ origin: "feed" }))).toBe("feed");
    expect(originBadge(doc({ origin: "operator_url" }))).toBe("aportat per l'operador");
    expect(originBadge(doc({ origin: "operator_upload" }))).toBe("pujat per l'operador");
    expect(originBadge(doc({ origin: "owner_upload" }))).toBe("pujat per l'empresa");
    expect(contributionBadge(doc({ contribution_status: "pending_verification" }))).toEqual({
      label: "pendent de verificació",
      level: "attention",
    });
    expect(contributionBadge(doc({ contribution_status: "verified" }))?.label).toBe("verificat");
    expect(contributionBadge(doc({ contribution_status: "rejected" }))?.label).toBe("rebutjat");
    expect(contributionBadge(doc({ contribution_status: "not_applicable" }))).toBeNull();
  });

  it("un retirat diu des de quan i qui l'ha retirat: la font només si el document era del feed (M3)", () => {
    expect(removedLabel(doc({ removed_at: "2026-09-30T10:00:00Z" }))).toMatch(/^Retirat de la font \(des del .+\)$/);
    expect(removedLabel(doc({ origin: "operator_url", removed_at: "2026-09-30T10:00:00Z" }))).toMatch(/^Retirat per l'operador \(des del .+\)$/);
    expect(removedLabel(doc({ origin: "operator_upload", removed_at: "2026-09-30T10:00:00Z" }))).toMatch(/^Retirat per l'operador/);
    expect(removedLabel(doc({ origin: "owner_upload", removed_at: "2026-09-30T10:00:00Z" }))).toMatch(/^Retirat per l'empresa/);
    expect(removedLabel(doc({ removed_at: null }))).toBeNull();
  });
});

describe("àlies plegats sota el canònic", () => {
  it("un àlies no és una fila pròpia si el canònic hi és; ho és si el canònic no hi és", () => {
    const canonical = doc({ id: "c", name: "PCAP.pdf" });
    const alias = doc({ id: "a", name: "PCAP còpia.pdf", duplicate_of_document_id: "c", analyzable: false });
    const orphan = doc({ id: "o", name: "Annex.pdf", duplicate_of_document_id: "x", analyzable: false });
    const all = [canonical, alias, orphan];
    expect(topLevelDocuments(all).map((d) => d.id)).toEqual(["c", "o"]);
    expect(aliasesOf(canonical, all).map((d) => d.id)).toEqual(["a"]);
  });
});

describe("N de M documents analitzables llegits", () => {
  it("M compta només els analitzables (del servidor) i N els que ja tenen text llegit", () => {
    const documents = [
      doc({ id: "1", latest_file: extracted }),
      doc({ id: "2", latest_file: null }),
      doc({ id: "3", analyzable: false, contribution_status: "pending_verification", latest_file: extracted }),
      doc({ id: "4", analyzable: false, duplicate_of_document_id: "1", latest_file: extracted }),
    ];
    expect(readingCount(documents)).toEqual({ read: 1, total: 2 });
    expect(readingLine(documents)).toBe("1 de 2 documents analitzables llegits");
    expect(readingLine([doc({ latest_file: extracted })])).toBe("1 de 1 document analitzable llegit");
    expect(readingLine([doc({ analyzable: false })])).toBe("Cap document analitzable");
  });
});

describe("forats del paquet", () => {
  const names = new Map([["d9", "Plec escanejat.pdf"]]);
  const gap = (overrides: Partial<TenderPackageGap>): TenderPackageGap => ({
    code: "required_role_missing",
    key: "ppt",
    would_provide: [],
    ...overrides,
  });

  it("diu què falta en humà, sense codis", () => {
    expect(gapText(gap({}), names)).toBe("Falta el plec de prescripcions tècniques (PPT)");
    expect(gapText(gap({ code: "referenced_document_missing", key: "DOC-03" }), names)).toBe(
      "Falta un document que cita el plec (DOC-03)",
    );
    expect(gapText(gap({ code: "no_text", key: "d9" }), names)).toBe("Plec escanejat.pdf: present, sense text llegible per a l'anàlisi");
    expect(gapText(gap({ code: "format_not_analyzable", key: "zz" }), names)).toBe(
      "Un document: present, en un format que Tuvi encara no pot llegir",
    );
    expect(gapText(gap({ code: "annex_missing", key: null }), names)).toBe("Falta un annex");
    expect(gapText(gap({ code: "declared_by_operator", key: "x" }), names)).toBe(
      "Falta un document (declarat per l'operador)",
    );
    expect(gapText(gap({ key: "una_cosa_rara" }), names)).toBe("Falta un document obligatori");
  });

  it("l'impacte: què aportaria, o «impacte desconegut» si no se sap", () => {
    expect(gapImpact(gap({ would_provide: ["requirements", "scope_items"] }))).toBe(
      "aportaria: requisits, abast",
    );
    expect(gapImpact(gap({ would_provide: [] }))).toBe("impacte desconegut");
    expect(gapImpact(gap({ would_provide: ["algo_nou"] }))).toBe("aportaria: altres dades");
  });
});
