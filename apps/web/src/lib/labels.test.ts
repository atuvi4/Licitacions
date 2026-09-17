import { describe, expect, it } from "vitest";

import {
  ANALYSIS_BASIS_LABELS,
  ELIGIBILITY_LABELS,
  RECOMMENDATION_LABELS,
  RESULT_LABELS,
} from "@/lib/compliance";
import { AVAILABILITY_LABELS, TEMPORAL_STATUS_LABELS, VERIFICATION_STATUS_LABELS } from "@/lib/format";
import type { JobRequest } from "@/lib/jobs";
import { MATCH_STATUS_LABELS } from "@/lib/matching";
import { ANALYSIS_STATE_LABELS, type ExtractionRun, type RequirementAnalysis } from "@/lib/requirements";
import { DOCUMENT_STATE_LABELS, TENDER_STATUS_LABELS } from "@/lib/tenders";
import {
  CHECKLIST_STATUS_LABELS,
  COVERAGE_LABELS,
  DECISION_LABELS,
  EVIDENCE_STATUS_LABELS,
  PACKAGE_STATUS_LABELS,
  READINESS_LABELS,
  RELEVANCE_LABELS,
  TASK_STATUS_LABELS,
  VALIDITY_LABELS,
  WORKSPACE_STATUS_LABELS,
  type WorkspaceStatus,
} from "@/lib/workspace";

import {
  ANALYSIS_BASIS_LEVEL,
  ANALYSIS_STATE_LEVEL,
  ANALYSIS_STATUS_TEXT,
  AVAILABILITY_LEVEL,
  BLOCKER_LEVEL,
  CHECKLIST_LEVEL,
  COMPLETENESS_LEVEL,
  COVERAGE_LEVEL,
  DECISION_LEVEL,
  DOCUMENT_STATE_LEVEL,
  ELIGIBILITY_LEVEL,
  EVIDENCE_STATUS_LEVEL,
  JOB_ERROR_ACTIONS,
  JOB_LEVEL,
  MATCH_STATUS_LEVEL,
  PACKAGE_STATUS_LEVEL,
  READINESS_LEVEL,
  RECOMMENDATION_LEVEL,
  RELEVANCE_LEVEL,
  RESULT_LEVEL,
  ROLE_LABELS,
  TASK_LEVEL,
  TEMPORAL_LEVEL,
  TENDER_STATUS_LEVEL,
  VALIDITY_LEVEL,
  VERIFICATION_LEVEL,
  WORKSPACE_STATUS_LEVEL,
  activeLevel,
  QA_LEVEL,
  changeLevel,
  cpvPreferenceLevel,
  deadlineLevel,
  jobErrorAction,
  jobLevel,
  roleLabel,
  stepOf,
} from "./labels";

const LEVELS = new Set(["neutral", "running", "ready", "attention", "blocking", "action"]);

function expectExhaustive(levelMap: Record<string, unknown>, labels: Record<string, string>, name: string) {
  const expected = Object.keys(labels).sort();
  const actual = Object.keys(levelMap).sort();
  expect(actual, `${name}: mateixes claus que el diccionari d'etiquetes`).toEqual(expected);
  for (const value of Object.values(levelMap)) {
    expect(LEVELS.has(value as string), `${name}: nivell desconegut «${String(value)}»`).toBe(true);
  }
}

describe("mapes de nivell: cada valor de cada enum té nivell", () => {
  it("compliment i elegibilitat", () => {
    expectExhaustive(RESULT_LEVEL, RESULT_LABELS, "RESULT_LEVEL");
    expectExhaustive(ELIGIBILITY_LEVEL, ELIGIBILITY_LABELS, "ELIGIBILITY_LEVEL");
    expectExhaustive(RECOMMENDATION_LEVEL, RECOMMENDATION_LABELS, "RECOMMENDATION_LEVEL");
    expectExhaustive(ANALYSIS_BASIS_LEVEL, ANALYSIS_BASIS_LABELS, "ANALYSIS_BASIS_LEVEL");
  });

  it("candidatura, dossier i paquet", () => {
    expectExhaustive(WORKSPACE_STATUS_LEVEL, WORKSPACE_STATUS_LABELS, "WORKSPACE_STATUS_LEVEL");
    expectExhaustive(DECISION_LEVEL, DECISION_LABELS, "DECISION_LEVEL");
    expectExhaustive(READINESS_LEVEL, READINESS_LABELS, "READINESS_LEVEL");
    expectExhaustive(COVERAGE_LEVEL, COVERAGE_LABELS, "COVERAGE_LEVEL");
    expectExhaustive(EVIDENCE_STATUS_LEVEL, EVIDENCE_STATUS_LABELS, "EVIDENCE_STATUS_LEVEL");
    expectExhaustive(VALIDITY_LEVEL, VALIDITY_LABELS, "VALIDITY_LEVEL");
    expectExhaustive(RELEVANCE_LEVEL, RELEVANCE_LABELS, "RELEVANCE_LEVEL");
    expectExhaustive(PACKAGE_STATUS_LEVEL, PACKAGE_STATUS_LABELS, "PACKAGE_STATUS_LEVEL");
    expectExhaustive(CHECKLIST_LEVEL, CHECKLIST_STATUS_LABELS, "CHECKLIST_LEVEL");
    expectExhaustive(TASK_LEVEL, TASK_STATUS_LABELS, "TASK_LEVEL");
  });

  it("anàlisi, plecs, licitacions i pre-fit", () => {
    expectExhaustive(ANALYSIS_STATE_LEVEL, ANALYSIS_STATE_LABELS, "ANALYSIS_STATE_LEVEL");
    expectExhaustive(DOCUMENT_STATE_LEVEL, DOCUMENT_STATE_LABELS, "DOCUMENT_STATE_LEVEL");
    expectExhaustive(TENDER_STATUS_LEVEL, TENDER_STATUS_LABELS, "TENDER_STATUS_LEVEL");
    expectExhaustive(MATCH_STATUS_LEVEL, MATCH_STATUS_LABELS, "MATCH_STATUS_LEVEL");
    expectExhaustive(TEMPORAL_LEVEL, TEMPORAL_STATUS_LABELS, "TEMPORAL_LEVEL");
  });

  it("bloquejos: cap pastilla per a «none», Atenció per a «possible», Bloqueig per a «confirmat»", () => {
    expect(BLOCKER_LEVEL.none).toBeNull();
    expect(BLOCKER_LEVEL.potential).toBe("attention");
    expect(BLOCKER_LEVEL.confirmed).toBe("blocking");
  });
});

describe("els cinc invariants de la secció 0.4", () => {
  it("«sense determinar» és Neutre, mai verd ni bloqueig", () => {
    expect(ELIGIBILITY_LEVEL.undetermined).toBe("neutral");
  });
  it("«sense dades» és Atenció, mai un no", () => {
    expect(RESULT_LEVEL.unknown).toBe("attention");
  });
  it("un document triat no és evidència acreditada", () => {
    expect(EVIDENCE_STATUS_LEVEL.selected).not.toBe("ready");
  });
  it("una anàlisi parcial mai és A punt", () => {
    expect(ANALYSIS_STATE_LEVEL.partial).toBe("attention");
  });
  it("caducar abans del termini bloqueja", () => {
    expect(VALIDITY_LEVEL.expires_before_deadline).toBe("blocking");
  });
});

describe("deadlineLevel", () => {
  it.each([
    [null, "neutral"],
    [-3, "blocking"],
    [0, "blocking"],
    [7, "blocking"],
    [8, "attention"],
    [21, "attention"],
    [22, "neutral"],
    [60, "neutral"],
  ] as const)("%s dies → %s", (days, level) => {
    expect(deadlineLevel(days)).toBe(level);
  });
});

describe("stepOf: els deu estats es projecten sobre els tres passos", () => {
  it.each<[WorkspaceStatus, ReturnType<typeof stepOf>]>([
    ["analysis", "decidir"],
    ["needs_review", "decidir"],
    ["awaiting_decision", "decidir"],
    ["go_approved", "preparar"],
    ["preparing_bid", "preparar"],
    ["ready_for_final_review", "tancar"],
    ["approved_for_submission", "tancar"],
    ["submitted", "tancada"],
    ["no_go_approved", "tancada"],
    ["withdrawn", "tancada"],
  ])("%s → %s", (status, step) => {
    expect(stepOf(status)).toBe(step);
  });
});

describe("accions per codi d'error", () => {
  it("cada codi té títol i una acció coneguda", () => {
    const kinds = new Set(["retry", "choose_documents", "process_first", "wait", "job_center", "none"]);
    for (const [code, entry] of Object.entries(JOB_ERROR_ACTIONS)) {
      expect(entry.title.length, code).toBeGreaterThan(0);
      expect(entry.title, `${code}: el títol no pot ser el codi`).not.toContain(code);
      expect(kinds.has(entry.action.kind), `${code}: acció ${entry.action.kind}`).toBe(true);
      if (entry.action.kind !== "none") {
        expect(entry.action.label?.length ?? 0, `${code}: una acció té etiqueta`).toBeGreaterThan(0);
      }
    }
  });

  it("la taula 3.2 del handoff", () => {
    expect(JOB_ERROR_ACTIONS.analysis_too_large.title).toBe("El plec és massa gran per llegir-lo d'un cop");
    expect(JOB_ERROR_ACTIONS.analysis_too_large.action.kind).toBe("choose_documents");
    expect(JOB_ERROR_ACTIONS.company_active_limit.action.kind).toBe("wait");
    expect(JOB_ERROR_ACTIONS.input_changed.action.kind).toBe("retry");
    expect(JOB_ERROR_ACTIONS.nothing_analyzable.action.kind).toBe("process_first");
    expect(JOB_ERROR_ACTIONS.no_documents.action.kind).toBe("process_first");
    for (const code of ["worker_lost", "worker_lost_before_start", "lease_expired", "operation_busy", "internal_error", "all_chunks_failed"]) {
      expect(JOB_ERROR_ACTIONS[code].action.kind, code).toBe("retry");
    }
    expect(JOB_ERROR_ACTIONS.permission_denied.action.kind).toBe("none");
    expect(JOB_ERROR_ACTIONS.permission_denied.title).toBe("El teu rol no pot llançar anàlisis");
    for (const code of ["provider_changed", "provider_not_configured", "analysis_in_progress"]) {
      expect(JOB_ERROR_ACTIONS[code].action.kind, code).toBe("none");
    }
  });

  it("un codi desconegut o absent no arriba mai al text", () => {
    expect(jobErrorAction("something_new").title).toBe("No s'ha pogut completar");
    expect(jobErrorAction("something_new").action.kind).toBe("none");
    expect(jobErrorAction(null).title).toBe("No s'ha pogut completar");
    expect(jobErrorAction("analysis_too_large")).toBe(JOB_ERROR_ACTIONS.analysis_too_large);
  });
});

describe("feina en segon pla", () => {
  const base: JobRequest = {
    id: "j1",
    job_type: "analyze_tender_requirements",
    tender_id: "t1",
    status: "accepted",
    outcome: null,
    reused_existing: false,
    result_run_id: null,
    error_code: null,
    requested_at: "2026-09-16T10:00:00Z",
    finished_at: null,
    progress: null,
  };

  it("JOB_LEVEL i jobLevel: partial cau a Atenció", () => {
    expect(JOB_LEVEL.accepted).toBe("running");
    expect(JOB_LEVEL.succeeded).toBe("ready");
    expect(JOB_LEVEL.failed).toBe("blocking");
    expect(JOB_LEVEL.cancelled).toBe("neutral");
    expect(jobLevel({ ...base, status: "succeeded", outcome: "partial" })).toBe("attention");
    expect(jobLevel({ ...base, status: "succeeded", outcome: "executed" })).toBe("ready");
  });
});

describe("ANALYSIS_STATUS_TEXT", () => {
  const run: ExtractionRun = {
    id: "r1",
    tender_id: "t1",
    status: "completed",
    is_current: true,
    input_fingerprint: "f",
    extractor_version: "v1",
    prompt_version: "p1",
    schema_version: "s1",
    provider: "fake",
    model: "fake",
    analysis_basis: "real",
    started_at: "2026-09-03T09:00:00Z",
    completed_at: "2026-09-03T09:05:00Z",
    documents_analyzed: 2,
    pages_analyzed: 41,
    chunks_total: 14,
    chunks_completed: 14,
    chunks_failed: 0,
    requirements_extracted: 38,
    requirements_rejected: 0,
    award_criteria_extracted: 3,
    evidence_rejected: 0,
    validation_errors: 0,
    input_tokens: null,
    output_tokens: null,
    estimated_cost: null,
    duration_ms: null,
    skipped_documents: null,
    error_code: null,
    error_message: null,
  };
  const analysis = (overrides: Partial<ExtractionRun>, is_stale = false): RequirementAnalysis => ({
    run: { ...run, ...overrides },
    requirements: [],
    award_criteria: [],
    is_stale,
  });
  const job = (progress: JobRequest["progress"]): JobRequest => ({
    id: "j1",
    job_type: "analyze_tender_requirements",
    tender_id: "t1",
    status: "accepted",
    outcome: null,
    reused_existing: false,
    result_run_id: null,
    error_code: null,
    requested_at: "2026-09-16T10:00:00Z",
    finished_at: null,
    progress,
  });

  it("completa: requisits i data", () => {
    expect(ANALYSIS_STATUS_TEXT(analysis({}), null)).toMatch(/^Anàlisi completa · 38 requisits · .*2026$/);
  });
  it("en marxa amb progrés o sense", () => {
    expect(ANALYSIS_STATUS_TEXT(analysis({}), job({ done: 3, total: 14 }))).toBe("Analitzant · 3 de 14");
    expect(ANALYSIS_STATUS_TEXT(analysis({}), job(null))).toBe("En marxa · la font no informa del progrés");
    expect(ANALYSIS_STATUS_TEXT(analysis({ status: "running" }), null)).toBe("En marxa · la font no informa del progrés");
  });
  it("parcial: documents omesos, mai «completa»", () => {
    const partial = analysis({
      status: "partial",
      skipped_documents: [{ tender_document_id: "d", document_name: "Plec tècnic", document_type: "technical_terms", reason: "document_not_processed" }],
    });
    expect(ANALYSIS_STATUS_TEXT(partial, null)).toBe("Anàlisi parcial · 1 document omès");
  });
  it("obsoleta: la data i que el plec ha canviat", () => {
    expect(ANALYSIS_STATUS_TEXT(analysis({}, true), null)).toMatch(/^Anàlisi del .*2026 · el plec ha canviat$/);
  });
  it("sense anàlisi i fallida", () => {
    expect(ANALYSIS_STATUS_TEXT(null, null)).toBe("Plec sense analitzar");
    expect(ANALYSIS_STATUS_TEXT(analysis({ status: "failed" }), null)).toBe("L'anàlisi ha fallat");
  });
});

describe("rols humanitzats", () => {
  it("cobreix els cinc rols de MembershipRole i deixa el valor cru si no el coneix", () => {
    expect(Object.keys(ROLE_LABELS).sort()).toEqual(["admin", "bid_manager", "owner", "technical", "viewer"]);
    expect(roleLabel("bid_manager")).toBe("Responsable de candidatures");
    expect(roleLabel("auditor")).toBe("auditor");
  });
});

describe("nivells del perfil (L8)", () => {
  it("disponibilitat, verificació i completitud tenen nivell per a cada valor", () => {
    expect(Object.keys(AVAILABILITY_LEVEL).sort()).toEqual(Object.keys(AVAILABILITY_LABELS).sort());
    expect(Object.keys(VERIFICATION_LEVEL).sort()).toEqual(Object.keys(VERIFICATION_STATUS_LABELS).sort());
    expect(Object.keys(COMPLETENESS_LEVEL).sort()).toEqual(["complete", "empty", "partial"]);
    expect(COMPLETENESS_LEVEL.empty).toBe("blocking");
    expect(COMPLETENESS_LEVEL.partial).toBe("attention");
    expect(COMPLETENESS_LEVEL.complete).toBe("ready");
    expect(VERIFICATION_LEVEL.verified).toBe("ready");
    expect(VERIFICATION_LEVEL.pending_verification).toBe("neutral");
  });

  it("els booleans actiu/exclòs surten d'una funció del diccionari, no d'un if al JSX", () => {
    expect(activeLevel(true)).toBe("ready");
    expect(activeLevel(false)).toBe("neutral");
    expect(cpvPreferenceLevel({ active: true, excluded: true })).toBe("blocking");
    expect(cpvPreferenceLevel({ active: true, excluded: false })).toBe("ready");
    expect(cpvPreferenceLevel({ active: false, excluded: false })).toBe("neutral");
  });
});

describe("changeLevel (L9)", () => {
  it("un canvi material demana atenció; un d'informatiu és neutre", () => {
    expect(changeLevel({ is_material: true })).toBe("attention");
    expect(changeLevel({ is_material: false })).toBe("neutral");
  });
});

describe("QA_LEVEL (L10)", () => {
  it("bona → a punt, avisos → atenció, pobra → bloqueig", () => {
    expect(QA_LEVEL).toEqual({ good: "ready", warning: "attention", poor: "blocking" });
  });
});
