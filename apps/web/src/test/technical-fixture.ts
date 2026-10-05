import type { Requirement, RequirementAnalysis } from "@/lib/requirements";
import type { TechnicalSolution } from "@/lib/technical";

/** Un requisit del plec amb una cita literal real. */
export function requirementFixture(overrides: Partial<Requirement> = {}): Requirement {
  const id = overrides.id ?? "r1";
  return {
    id,
    run_id: "run1",
    origin: "extracted" as const,
    archived_at: null,
    citations: [],
    timing: "unknown" as const,
    tender_id: "t1",
    tender_lot_id: null,
    category: "technical_requirement",
    obligation: "mandatory",
    routing: "technical_solution",
    operator: null,
    scope: "tender",
    title: "Commutadors amb doble alimentació",
    description: null,
    value_json: null,
    raw_lot_labels: null,
    confidence: "high",
    review_status: "verified",
    review_note: null,
    manual_override_json: null,
    reviewed_at: null,
    has_conflict: false,
    conflict_group: null,
    created_at: "2026-09-10T09:00:00Z",
    evidence: [
      {
        id: `${id}-e1`,
        extraction_id: "x1",
        page_id: "p1",
        tender_document_id: "d1",
        page_number: 14,
        quote: "Els commutadors d'accés disposaran de doble alimentació redundant.",
        char_start: null,
        char_end: null,
        section: null,
        evidence_role: "primary",
        extraction_revision: 2,
        document_name: "Plec tècnic",
      },
    ],
    ...overrides,
  };
}

export function analysisFixture(requirements: Requirement[]): RequirementAnalysis {
  return {
    run: {
      id: "run1",
      tender_id: "t1",
      status: "completed",
    } as RequirementAnalysis["run"],
    requirements,
    award_criteria: [],
    is_stale: false,
  };
}

/** Una solució lligada a un requisit, en redacció, sense validar. */
export function solutionFixture(overrides: Partial<TechnicalSolution> = {}): TechnicalSolution {
  return {
    id: "s1",
    workspace_id: "w1",
    requirement_id: "r1",
    title: "Commutació d'accés amb redundància",
    proposed_solution: "Es desplegaran 14 commutadors amb doble font.",
    supplier_name: "Aruba",
    product_name: "CX 6300M",
    owner_id: "u1",
    status: "drafting",
    source: "requirement",
    notes: null,
    validated_by: null,
    validated_at: null,
    validated_note: null,
    validated_requirement_fingerprint: null,
    validated_content_fingerprint: null,
    validation_stale: false,
    validation_stale_reasons: [],
    created_by: "u1",
    updated_by: "u1",
    created_at: "2026-09-12T09:00:00Z",
    updated_at: "2026-09-12T09:00:00Z",
    ...overrides,
  };
}
