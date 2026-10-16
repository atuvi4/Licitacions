import { describe, expect, it } from "vitest";

import { type ExtractionRun, historicRuns, notCurrentReason } from "./requirements";

function run(overrides: Partial<ExtractionRun> = {}): ExtractionRun {
  return {
    id: "x1",
    tender_id: "t1",
    status: "completed",
    is_current: false,
    input_fingerprint: "f",
    extractor_version: "v",
    prompt_version: "p",
    schema_version: "1",
    provider: "anthropic",
    model: "m",
    analysis_basis: "real",
    started_at: "2026-10-14T10:00:00Z",
    completed_at: null,
    documents_analyzed: 0,
    pages_analyzed: 0,
    chunks_total: 0,
    chunks_completed: 0,
    chunks_failed: 0,
    requirements_extracted: 0,
    requirements_rejected: 0,
    award_criteria_extracted: 0,
    evidence_rejected: 0,
    validation_errors: 0,
    input_tokens: null,
    output_tokens: null,
    estimated_cost: null,
    duration_ms: null,
    skipped_documents: null,
    error_code: null,
    error_message: null,
    run_basis: "real",
    needs_review: false,
    ...overrides,
  };
}

describe("historicRuns (P2-01)", () => {
  it("treu la vigent, l'efectiva i les que encara corren", () => {
    const runs = [
      run({ id: "cur", is_current: true }),
      run({ id: "eff" }),
      run({ id: "old" }),
      run({ id: "pen", status: "pending" }),
      run({ id: "run", status: "running" }),
    ];
    expect(historicRuns(runs, "eff").map((item) => item.id)).toEqual(["old"]);
  });

  it("sense dades (o amb una resposta que no és una llista) no hi ha historial", () => {
    expect(historicRuns(null, null)).toEqual([]);
    expect(historicRuns({} as unknown as ExtractionRun[], null)).toEqual([]);
  });
});

describe("notCurrentReason (P2-01)", () => {
  it("cada motiu es diu en llenguatge de producte", () => {
    expect(notCurrentReason(run({ run_basis: "simulation" }))).toMatch(/^Simulació/);
    expect(notCurrentReason(run({ error_code: "experimental_profile_not_current" }))).toMatch(/^Perfil experimental/);
    expect(notCurrentReason(run({ error_code: "forced_not_canonical" }))).toMatch(/forçada per l'operador/);
    expect(notCurrentReason(run({ status: "failed", error_code: "all_chunks_failed" }))).toMatch(/^Ha fallat/);
    expect(notCurrentReason(run({ status: "partial" }))).toMatch(/^Lectura parcial/);
    expect(notCurrentReason(run({ status: "completed_empty" }))).toMatch(/sense cap requisit ni criteri/);
    expect(notCurrentReason(run())).toMatch(/^No és la vigent/);
  });

  it("una simulació es diu simulació encara que hagi acabat bé", () => {
    expect(notCurrentReason(run({ run_basis: "unconfigured", status: "completed" }))).toMatch(/^Simulació/);
  });
});
