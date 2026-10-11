import { describe, expect, it } from "vitest";

import {
  OPERATOR_ACTION_LABELS,
  canReviewRole,
  documentsPath,
  effectiveRoleOf,
  operatorActions,
  type PlatformClassification,
  type PlatformTenderDocument,
} from "./platform-documents";

const SHA = "a".repeat(64);

function doc(overrides: Partial<PlatformTenderDocument> = {}): PlatformTenderDocument {
  return {
    id: "d1",
    name: "PCAP.pdf",
    document_type: "administrative_terms",
    source_document_type_code: null,
    url: "https://contractaciopublica.cat/pcap.pdf",
    mime_type: "application/pdf",
    published_at: null,
    removed_at: null,
    latest_file: {
      file_id: "f1",
      version_number: 1,
      download_status: "downloaded",
      origin: "fetched",
      file_size: 100,
      sha256: SHA,
      mime_type: "application/pdf",
      text_extraction_supported: true,
      analysis_status: "extracted",
      extraction_status: "extracted",
      quality: "good",
      page_count: 3,
      text_char_count: 1200,
      ocr_candidate: false,
      extraction_id: "e1",
      processed_at: "2026-10-01T10:00:00Z",
      error_code: null,
    },
    origin: "feed",
    contribution_status: "not_applicable",
    duplicate_of_document_id: null,
    analyzable: true,
    governing_source_id: "s1",
    contributed_by: null,
    contributed_at: null,
    contribution_source_url: null,
    verified_by: null,
    verified_at: null,
    verification_note: null,
    retired_by: null,
    retired_reason: null,
    created_at: "2026-10-01T09:00:00Z",
    ...overrides,
  };
}

describe("operatorActions (3B-2 §2.6: què pot fer l'operador amb cada document)", () => {
  it("els documents del feed i les pujades de l'empresa no s'operen", () => {
    expect(operatorActions(doc({ origin: "feed" }))).toEqual([]);
    expect(operatorActions(doc({ origin: "owner_upload" }))).toEqual([]);
  });

  it("una aportació pendent es verifica, es rebutja o es retira", () => {
    expect(operatorActions(doc({ origin: "operator_url", contribution_status: "pending_verification", analyzable: false }))).toEqual([
      "verify",
      "reject",
      "retire",
    ]);
    expect(operatorActions(doc({ origin: "operator_upload", contribution_status: "pending_verification", analyzable: false }))).toEqual([
      "verify",
      "reject",
      "retire",
    ]);
  });

  it("una aportació verificada es pot retirar; només la d'URL es pot cedir al feed", () => {
    expect(operatorActions(doc({ origin: "operator_url", contribution_status: "verified" }))).toEqual(["retire", "hand_over"]);
    expect(operatorActions(doc({ origin: "operator_upload", contribution_status: "verified" }))).toEqual(["retire"]);
  });

  it("una de rebutjada només es pot retirar; una de retirada només es pot restaurar", () => {
    expect(operatorActions(doc({ origin: "operator_url", contribution_status: "rejected", analyzable: false }))).toEqual(["retire"]);
    expect(operatorActions(doc({ origin: "operator_upload", contribution_status: "verified", removed_at: "2026-10-02T00:00:00Z", analyzable: false }))).toEqual([
      "restore",
    ]);
  });

  it("cada acció té etiqueta humana", () => {
    for (const action of ["verify", "reject", "retire", "restore", "hand_over"] as const) {
      expect(OPERATOR_ACTION_LABELS[action]).toMatch(/\S/);
      expect(OPERATOR_ACTION_LABELS[action]).not.toMatch(/_/);
    }
  });
});

describe("canReviewRole", () => {
  it("només un document analitzable amb fitxer vigent (sha) es pot revisar", () => {
    expect(canReviewRole(doc())).toBe(true);
    expect(canReviewRole(doc({ latest_file: null }))).toBe(false);
    expect(canReviewRole(doc({ analyzable: false }))).toBe(false);
    expect(canReviewRole(doc({ removed_at: "2026-10-02T00:00:00Z", analyzable: false }))).toBe(false);
  });
});

describe("effectiveRoleOf i rutes", () => {
  const classification: PlatformClassification = {
    run: null,
    suggestions: [],
    findings: [],
    reviews: [],
    effective_roles: [
      { tender_document_id: "d1", role: "pcap", document_type: "administrative_terms", source: "global_classifier", content_sha256: SHA, fallback_source: null },
    ],
    gaps: [],
  };

  it("troba el rol efectiu d'un document, o null", () => {
    expect(effectiveRoleOf(classification, "d1")?.role).toBe("pcap");
    expect(effectiveRoleOf(classification, "d9")).toBeNull();
    expect(effectiveRoleOf(null, "d1")).toBeNull();
  });

  it("les rutes de l'operador pengen de /platform/tenders/{id}", () => {
    expect(documentsPath("t1")).toBe("/platform/tenders/t1/documents");
  });
});
