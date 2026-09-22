import { describe, expect, it } from "vitest";

import type { JobRequest } from "@/lib/jobs";
import type { TenderDocumentFileSummary } from "@/lib/tenders";

import {
  FILE_ERROR_MESSAGES,
  extractedText,
  fileErrorText,
  jobPill,
  readingSubtext,
  selectionLabel,
  versionText,
} from "./documents";

function file(overrides: Partial<TenderDocumentFileSummary>): TenderDocumentFileSummary {
  return {
    file_id: "f1",
    version_number: 1,
    download_status: "downloaded",
    origin: "fetched",
    file_size: 120000,
    sha256: "abc",
    mime_type: "application/pdf",
    text_extraction_supported: true,
    analysis_status: "extracted",
    extraction_status: "extracted",
    quality: "good",
    page_count: 12,
    text_char_count: 34567,
    ocr_candidate: false,
    extraction_id: "e1",
    processed_at: "2026-09-15T10:00:00Z",
    error_code: null,
    ...overrides,
  };
}

function job(overrides: Partial<JobRequest>): JobRequest {
  return {
    id: "j1",
    job_type: "process_tender_documents",
    tender_id: "t1",
    status: "accepted",
    outcome: null,
    reused_existing: false,
    result_run_id: null,
    error_code: null,
    requested_at: "2026-09-16T10:00:00Z",
    finished_at: null,
    progress: null,
    ...overrides,
  };
}

describe("text extret", () => {
  it("pàgines i caràcters reals, o «—» si no hi ha extracció", () => {
    expect(extractedText(file({}))).toBe("12 pàg. · 34.567 caràcters");
    expect(extractedText(file({ page_count: 1, text_char_count: 900 }))).toBe("1 pàg. · 900 caràcters");
    expect(extractedText(file({ extraction_id: null, page_count: null, text_char_count: null }))).toBe("—");
    expect(extractedText(null)).toBe("—");
  });

  it("subtext de lectura només per a possibles escanejos o poc text", () => {
    expect(readingSubtext(file({}))).toBeNull();
    expect(readingSubtext(file({ ocr_candidate: true }))).toBe("possible escaneig · sense OCR");
    expect(readingSubtext(file({ quality: "low_text" }))).toBe("poc text extret");
    expect(readingSubtext(null)).toBeNull();
  });

  it("la versió diu el número i la data del processat", () => {
    expect(versionText(file({ version_number: 2, processed_at: "2026-09-15T10:00:00Z" }))).toMatch(/^v2 · /);
    expect(versionText(file({ processed_at: null }))).toBe("v1");
    expect(versionText(null)).toBeNull();
  });
});

describe("errors de fitxer en llenguatge humà", () => {
  it("els codis reals de descàrrega i extracció tenen frase; un de desconegut cau al genèric", () => {
    for (const code of ["http_error", "too_large", "timeout", "network_error", "not_found", "unsupported_for_text_extraction", "stale"]) {
      expect(FILE_ERROR_MESSAGES[code], code).toBeTruthy();
      expect(fileErrorText(code)).toBe(FILE_ERROR_MESSAGES[code]);
      expect(fileErrorText(code)).not.toContain(code);
    }
    expect(fileErrorText("codi_nou")).toBe("No s'ha pogut processar aquest document.");
    expect(fileErrorText(null)).toBe("No s'ha pogut processar aquest document.");
  });
});

describe("pastilla de la feina", () => {
  it("en curs amb progrés, en curs sense, acabada, parcial, fallada; cap petició → cap pastilla", () => {
    expect(jobPill(null)).toBeNull();
    expect(jobPill(job({ progress: { done: 1, total: 3 } }))).toEqual({ level: "running", label: "Processant · 1 de 3" });
    expect(jobPill(job({}))).toEqual({ level: "running", label: "Processant" });
    expect(jobPill(job({ status: "succeeded", outcome: "executed" }))).toEqual({ level: "ready", label: "Processat" });
    expect(jobPill(job({ status: "succeeded", outcome: "partial" }))).toEqual({ level: "attention", label: "Processat amb avisos" });
    expect(jobPill(job({ status: "failed", error_code: "worker_lost" }))).toEqual({ level: "blocking", label: "No s'ha pogut processar" });
  });
});

describe("peu de selecció", () => {
  it("compta els triats en singular i plural", () => {
    expect(selectionLabel(1)).toEqual({ count: "1 triat", action: "Processa 1 document" });
    expect(selectionLabel(3)).toEqual({ count: "3 triats", action: "Processa 3 documents" });
  });
});
