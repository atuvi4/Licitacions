import { describe, expect, it } from "vitest";

import {
  PREPARE_READ_ONLY_TEXT,
  READINESS_BLOCKER_LABELS,
  STALE_REASON_LABELS,
  isPrepareEditable,
  readinessBlockerLabel,
  staleReasonLabel,
  warningLabel,
  workspaceStatusLabel,
} from "./workspace";

/** Els codis que emet `app/workspace/dossier.py` (BLOCKER_*). Si el backend n'afegeix un, aquesta prova ho diu. */
const BACKEND_READINESS_BLOCKERS = [
  "decision_not_go",
  "analysis_missing",
  "mandatory_unresolved",
  "analysis_outdated",
  "mandatory_checklist_blocked",
  "mandatory_evidence_missing",
  "mandatory_document_expired",
  "mandatory_tasks_open",
  "mandatory_relevance_unproven",
  "evidence_needs_review",
];

describe("readinessBlockerLabel", () => {
  it("tots els blocadors que emet el backend tenen text humà", () => {
    for (const code of BACKEND_READINESS_BLOCKERS) {
      expect(READINESS_BLOCKER_LABELS[code], code).toBeTruthy();
      expect(readinessBlockerLabel(code)).toBe(READINESS_BLOCKER_LABELS[code]);
      expect(readinessBlockerLabel(code)).not.toContain("_");
    }
  });

  it("un codi desconegut no arriba mai al text de negoci", () => {
    expect(readinessBlockerLabel("whatever_new_code")).toBe("Bloqueig de preparació no reconegut");
  });
});

/** Els codis que emet `evaluate_staleness` i `approve_package` al backend. */
const BACKEND_STALE_REASONS = [
  "tender_revision_unknown",
  "tender_changed",
  "lot_removed",
  "tender_not_actionable",
  "compliance_run_changed",
  "requirement_run_changed",
  "manifest_changed",
  "document_expired_for_deadline",
  "superseded",
];

describe("staleReasonLabel", () => {
  it("tots els motius d'obsolescència del backend tenen text humà", () => {
    for (const code of BACKEND_STALE_REASONS) {
      expect(STALE_REASON_LABELS[code], code).toBeTruthy();
      expect(staleReasonLabel(code)).toBe(STALE_REASON_LABELS[code]);
    }
  });

  it("un codi desconegut no arriba mai al text de negoci", () => {
    expect(staleReasonLabel("brand_new_reason")).toBe("Motiu d'obsolescència no reconegut");
  });
});

describe("warningLabel", () => {
  it("un codi heretat d'Object.prototype dona el text neutre, mai «undefined»", () => {
    expect(warningLabel("toString")).toBe("Avís del compliment anterior");
    expect(warningLabel("constructor")).toBe("Avís del compliment anterior");
  });
});

describe("workspaceStatusLabel (A13)", () => {
  it("l'estat `analysis` s'etiqueta segons l'anàlisi real del plec; el valor de l'estat no canvia", () => {
    expect(workspaceStatusLabel({ status: "analysis", analysis_state: "absent" })).toBe("Pendent d'anàlisi");
    expect(workspaceStatusLabel({ status: "analysis", analysis_state: "stale" })).toBe("Anàlisi obsoleta");
    expect(workspaceStatusLabel({ status: "analysis", analysis_state: "current" })).toBe("En anàlisi");
    // El detall porta l'estat al dossier; la llista, a la fila.
    expect(workspaceStatusLabel({ status: "analysis", dossier_coverage: { analysis_state: "absent", mandatory_total: null } })).toBe("Pendent d'anàlisi");
    // Sense dada (API anterior) no s'inventa res: l'etiqueta de sempre.
    expect(workspaceStatusLabel({ status: "analysis" })).toBe("En anàlisi");
    expect(workspaceStatusLabel({ status: "preparing_bid", analysis_state: "absent" })).toBe("Preparant oferta");
  });
});

describe("Preparar sense GO (3B-2 §7.1.6, decisió 4)", () => {
  it("només un GO vigent obre l'edició: la decisió vigent és l'última", () => {
    expect(isPrepareEditable({ human_decision: "go" })).toBe(true);
    expect(isPrepareEditable({ human_decision: null })).toBe(false);
    expect(isPrepareEditable({ human_decision: "no_go" })).toBe(false);
    // Ajornar després d'un GO també tanca: `human_decision` ja porta l'última.
    expect(isPrepareEditable({ human_decision: "defer" })).toBe(false);
  });

  it("la copy del mode lectura és exactament la de la spec", () => {
    expect(PREPARE_READ_ONLY_TEXT).toBe("Es pot consultar; s'obre a edició amb GO");
  });
});
