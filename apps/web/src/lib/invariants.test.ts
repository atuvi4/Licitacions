import { describe, expect, it } from "vitest";

import {
  ANALYSIS_STATE_LEVEL,
  COVERAGE_LEVEL,
  DECISION_LEVEL,
  DECISION_PENDING_LEVEL,
  ELIGIBILITY_LEVEL,
  EVIDENCE_STATUS_LEVEL,
  PACKAGE_STATUS_LEVEL,
  RECOMMENDATION_LEVEL,
  RESULT_LEVEL,
  WORKSPACE_STATUS_LEVEL,
} from "./labels";
import { DECISION_LABELS, PACKAGE_STATUS_LABELS, WORKSPACE_STATUS_LABELS } from "./workspace";
import { RECOMMENDATION_LABELS } from "./compliance";

/**
 * Els invariants de negoci que la interfície no pot difuminar. Cada un és una
 * distinció que un color o una etiqueta podrien esborrar; aquí queden fixats.
 */
describe("invariants del Sprint 1", () => {
  it("undetermined no és ni un sí ni un no", () => {
    expect(ELIGIBILITY_LEVEL.undetermined).toBe("neutral");
    expect(RECOMMENDATION_LEVEL.undetermined).toBe("neutral");
    expect(RESULT_LEVEL.unknown).not.toBe("ready");
    expect(RESULT_LEVEL.unknown).not.toBe("blocking");
  });

  it("partial no és complete", () => {
    expect(ANALYSIS_STATE_LEVEL.partial).not.toBe("ready");
    expect(ANALYSIS_STATE_LEVEL.partial).not.toBe(ANALYSIS_STATE_LEVEL.completed);
  });

  it("la recomanació automàtica no és la decisió humana", () => {
    // Dos diccionaris diferents, amb valors diferents: cap recomanació es pot llegir com a «GO».
    expect(Object.keys(RECOMMENDATION_LABELS)).not.toContain("go");
    expect(RECOMMENDATION_LABELS.go_candidate).not.toBe(DECISION_LABELS.go);
    expect(DECISION_PENDING_LEVEL).toBe("attention");
    expect(DECISION_LEVEL.go).toBe("ready");
  });

  it("selected no és covered: només la cobertura del backend és a punt", () => {
    expect(EVIDENCE_STATUS_LEVEL.selected).toBe("neutral");
    expect(COVERAGE_LEVEL.covered).toBe("ready");
    expect(COVERAGE_LEVEL.needs_review).not.toBe("ready");
  });

  it("compliment no és dossier: dos mapes de nivell independents", () => {
    expect(Object.keys(RESULT_LEVEL)).not.toEqual(Object.keys(COVERAGE_LEVEL));
    expect(RESULT_LEVEL.satisfies).toBe("ready");
    expect(COVERAGE_LEVEL.missing_document).toBe("blocking");
  });

  it("aprovar un paquet no és presentar la candidatura", () => {
    expect(PACKAGE_STATUS_LABELS.approved).toBe("Aprovat per presentar");
    expect(PACKAGE_STATUS_LABELS.approved).not.toBe(WORKSPACE_STATUS_LABELS.submitted);
    expect(PACKAGE_STATUS_LEVEL.approved).toBe("ready");
    expect(WORKSPACE_STATUS_LEVEL.approved_for_submission).not.toBe(WORKSPACE_STATUS_LEVEL.no_go_approved);
    expect(Object.keys(PACKAGE_STATUS_LABELS)).not.toContain("submitted");
  });

  it("zero bloqueigs sense anàlisi no és «tot bé»", () => {
    // El component ho diu amb «Cap requisit avaluat»; el nivell que ho sosté és neutre, mai verd.
    expect(ELIGIBILITY_LEVEL.undetermined).not.toBe("ready");
    expect(ELIGIBILITY_LEVEL.eligible).toBe("ready");
  });
});
