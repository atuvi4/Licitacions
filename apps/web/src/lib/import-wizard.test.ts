import { describe, expect, it, vi } from "vitest";

import {
  EMPTY_STEP1,
  canImportTender,
  createPayload,
  failedRowState,
  fileSize,
  newTokens,
  step1Error,
  updatePayload,
  uploadRowState,
  type Step1Form,
} from "@/lib/import-wizard";
import type { TenderDocumentFileSummary } from "@/lib/tenders";

const tokens = { tender: "t-1", buyer: "b-1" };

function form(overrides: Partial<Step1Form> = {}): Step1Form {
  return { ...EMPTY_STEP1, title: "Serveis de comunicacions", ...overrides };
}

function file(overrides: Partial<TenderDocumentFileSummary> = {}): TenderDocumentFileSummary {
  return {
    file_id: "f1",
    version_number: 1,
    download_status: "downloaded",
    origin: "uploaded",
    file_size: 2048,
    sha256: "a".repeat(64),
    mime_type: "application/pdf",
    text_extraction_supported: true,
    analysis_status: "extracted",
    extraction_status: "extracted",
    quality: "good",
    page_count: 3,
    text_char_count: 900,
    ocr_candidate: false,
    extraction_id: "e1",
    processed_at: null,
    error_code: null,
    ...overrides,
  };
}

describe("qui pot importar", () => {
  it("deixa importar a qui decideix", () => {
    for (const role of ["owner", "admin", "bid_manager"]) {
      expect(canImportTender(role)).toBe(true);
    }
  });

  it("no ho deixa a qui no decideix", () => {
    for (const role of ["technical", "viewer", "", null, undefined]) {
      expect(canImportTender(role)).toBe(false);
    }
  });
});

describe("tokens", () => {
  it("la licitació i l'organisme en tenen un cadascun", () => {
    const value = newTokens();
    expect(value.tender).not.toBe(value.buyer);
  });

  it("dues obertures de l'assistent no comparteixen token", () => {
    expect(newTokens().tender).not.toBe(newTokens().tender);
  });
});

describe("validació del pas 1", () => {
  it("només el títol és obligatori", () => {
    expect(step1Error(form())).toBeNull();
    expect(step1Error(form({ title: "  " }))).toMatch(/títol/i);
  });

  it("triar un organisme existent obliga a triar-lo de veritat", () => {
    expect(step1Error(form({ buyerChoice: "existing" }))).toMatch(/organisme/i);
    expect(step1Error(form({ buyerChoice: "existing", buyerId: "b9" }))).toBeNull();
  });

  it("escriure'n un obliga a posar-hi nom", () => {
    expect(step1Error(form({ buyerChoice: "manual" }))).toMatch(/nom/i);
    expect(step1Error(form({ buyerChoice: "manual", buyerName: "Ajuntament" }))).toBeNull();
  });
});

describe("cos de creació", () => {
  it("porta el token de la licitació i el títol net", () => {
    expect(createPayload(form({ title: "  Neteja  " }), tokens)).toEqual({
      client_token: "t-1",
      title: "Neteja",
    });
  });

  it("no envia organisme si no se'n sap cap", () => {
    const body = createPayload(form(), tokens);
    expect(body).not.toHaveProperty("buyer_id");
    expect(body).not.toHaveProperty("buyer_manual");
  });

  it("envia l'un o l'altre, mai tots dos", () => {
    const existing = createPayload(form({ buyerChoice: "existing", buyerId: "b9" }), tokens);
    expect(existing.buyer_id).toBe("b9");
    expect(existing).not.toHaveProperty("buyer_manual");

    const manual = createPayload(form({ buyerChoice: "manual", buyerName: "Ajuntament" }), tokens);
    expect(manual.buyer_manual).toEqual({ client_token: "b-1", name: "Ajuntament" });
    expect(manual).not.toHaveProperty("buyer_id");
  });
});

describe("cos de correcció", () => {
  it("porta la revisió llegida i cap camp d'identitat", () => {
    const body = updatePayload(form(), tokens, 2);
    expect(body.expected_revision).toBe(2);
    for (const forbidden of ["id", "source_id", "external_id", "owner_company_id", "client_token"]) {
      expect(body).not.toHaveProperty(forbidden);
    }
  });

  it("buidar l'expedient l'esborra en comptes de no tocar-lo", () => {
    expect(updatePayload(form({ expediente_number: "" }), tokens, 1).expediente_number).toBeNull();
  });

  it("tornar a «no se sap» treu l'organisme", () => {
    expect(updatePayload(form({ buyerChoice: "unknown" }), tokens, 1).buyer_id).toBeNull();
  });

  it("manté el mateix token d'organisme que la creació", () => {
    const body = updatePayload(form({ buyerChoice: "manual", buyerName: "Ajuntament" }), tokens, 1);
    expect(body.buyer_manual).toEqual({ client_token: "b-1", name: "Ajuntament" });
  });
});

describe("estat d'una fila del pas 2", () => {
  it("separa sempre el fitxer del text", () => {
    const state = uploadRowState(file());
    expect(state.stored).toBe("Guardat");
    expect(state.analysis).toBe("Text llegit");
  });

  it("un format sense parser està guardat i no és cap error", () => {
    const state = uploadRowState(file({ analysis_status: "unsupported", text_extraction_supported: false }));
    expect(state.stored).toBe("Guardat");
    expect(state.tone).not.toBe("error");
    expect(state.analysis).toMatch(/no suportada/i);
  });

  it("un escaneig sense text ho diu sense prometre cap OCR", () => {
    const state = uploadRowState(file({ analysis_status: "no_usable_text", ocr_candidate: true }));
    expect(state.stored).toBe("Guardat");
    expect(state.analysis).toMatch(/sense text/i);
    expect(state.analysis).not.toMatch(/ocr/i);
  });

  it("només una pujada que no s'ha desat és un error", () => {
    expect(failedRowState("La xarxa ha fallat").tone).toBe("error");
    expect(failedRowState("La xarxa ha fallat").stored).toMatch(/no s'ha pujat/i);
  });
});

describe("mida", () => {
  it("es llegeix sense inventar-se precisió", () => {
    expect(fileSize(512)).toBe("512 B");
    expect(fileSize(2048)).toBe("2 kB");
    expect(fileSize(3 * 1024 * 1024)).toBe("3.0 MB");
    expect(fileSize(null)).toBe("—");
  });
});

describe("tokens per HTTP a la xarxa local", () => {
  it("sense randomUUID (context no segur) continuen sent UUID v4 diferents", () => {
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", { getRandomValues: real.getRandomValues.bind(real) });
    try {
      const value = newTokens();
      const v4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
      expect(value.tender).toMatch(v4);
      expect(value.buyer).toMatch(v4);
      expect(value.tender).not.toBe(value.buyer);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
