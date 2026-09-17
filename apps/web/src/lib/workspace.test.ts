import { describe, expect, it } from "vitest";

import { READINESS_BLOCKER_LABELS, STALE_REASON_LABELS, readinessBlockerLabel, staleReasonLabel } from "./workspace";

/** Els codis que emet `app/workspace/dossier.py` (BLOCKER_*). Si el backend n'afegeix un, aquesta prova ho diu. */
const BACKEND_READINESS_BLOCKERS = [
  "decision_not_go",
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
