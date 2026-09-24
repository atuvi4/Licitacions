import type { FinalReadiness, ReadinessFinding } from "@/lib/readiness";

/** Un problema qualsevol; els camps que no importen a la prova queden neutres. */
export function findingFixture(overrides: Partial<ReadinessFinding> = {}): ReadinessFinding {
  return {
    code: "mandatory_unresolved",
    severity: "blocker",
    domain: "dossier",
    entity_id: null,
    context: {},
    ...overrides,
  };
}

/** Una candidatura a punt: cap problema. Les proves hi afegeixen el que calgui. */
export function readinessFixture(overrides: Partial<FinalReadiness> = {}): FinalReadiness {
  const findings = overrides.findings ?? [];
  return {
    workspace_id: "w1",
    tender_id: "t1",
    ready_for_final_review: findings.every((finding) => finding.severity !== "blocker"),
    dossier: { state: "ready_for_final_review", blockers: [] },
    documents: { reviewed: true, stale: false, reviewed_at: "2026-09-17T09:00:00Z", documents: 2 },
    technical: {
      scope_reviewed: true,
      scope_stale: false,
      scope_reviewed_at: "2026-09-17T09:00:00Z",
      scope_reviewed_by: "u1",
      requirements_in_scope: 3,
      findings: [],
    },
    economic: { offer_exists: true, findings: [] },
    ...overrides,
    findings,
  };
}
