import { describe, expect, it } from "vitest";

import {
  INSUFFICIENT_CAUSE_HELP,
  MATCH_STATUS_HELP,
  factorSourceValue,
  insufficientCause,
  reasonLabel,
  profileGaps,
  shortSummary,
  sourceGaps,
  statusHelp,
  type MatchCommon,
  type MatchFactor,
} from "@/lib/matching";

function factor(dimension: MatchFactor["dimension"], result: MatchFactor["result"], reason_code: string): MatchFactor {
  return {
    dimension,
    result,
    reason_code,
    weight: 0.25,
    score: result === "unknown" ? null : 20,
    max_score: 25,
    company_value: null,
    source_value: null,
    detail: {},
  };
}

function match(factors: MatchFactor[], status: MatchCommon["status"] = "insufficient_data"): MatchCommon {
  return {
    id: "m1",
    score: null,
    status,
    exclusion_kind: null,
    exclusion_reason_code: null,
    cpv_score: null,
    geographic_score: null,
    economic_score: null,
    timing_score: null,
    data_coverage: 0.2,
    factors,
    rules_version: "test",
    calculated_at: "2026-10-14T19:44:39Z",
  };
}

// El cas de l'auditoria (P1-02): perfil d'Indaleccius sense CPV, territori ni capacitat; licitació amb tot.
const emptyProfile = [
  factor("cpv", "unknown", "CPV_NO_COMPANY_PREFERENCES"),
  factor("geography", "unknown", "TERRITORY_NO_COMPANY_DATA"),
  factor("economic", "unknown", "AMOUNT_NO_COMPANY_PREFERENCES"),
  factor("timing", "match", "DEADLINE_ADEQUATE"),
];
const thinSource = [
  factor("cpv", "unknown", "CPV_UNKNOWN"),
  factor("geography", "unknown", "TERRITORY_UNKNOWN"),
  factor("economic", "unknown", "AMOUNT_UNKNOWN"),
  factor("timing", "match", "DEADLINE_ADEQUATE"),
];
const mixed = [
  factor("cpv", "unknown", "CPV_NO_COMPANY_PREFERENCES"),
  factor("geography", "match", "TERRITORY_HIGH_PRIORITY"),
  factor("economic", "unknown", "AMOUNT_UNKNOWN"),
  factor("timing", "unknown", "DEADLINE_UNKNOWN"),
];

describe("causa de les dades insuficients del pre-fit (auditoria UI P1-02)", () => {
  it("perfil buit i licitació completa: la causa és el perfil, no la font", () => {
    const m = match(emptyProfile);
    expect(insufficientCause(m)).toBe("profile");
    expect(profileGaps(m)).toEqual(["cpv", "geography", "economic"]);
    expect(sourceGaps(m)).toEqual([]);
    expect(statusHelp(m)).toBe(INSUFFICIENT_CAUSE_HELP.profile);
    expect(statusHelp(m)).not.toMatch(/La font no publica/);
    // Amb el perfil buit el motor no arriba a mirar la licitació: no es pot afirmar que no li falti res.
    expect(statusHelp(m)).not.toMatch(/mancança de la licitació/);
    expect(shortSummary(m)).toBe("El perfil de l'empresa no té prou dades per valorar-la");
  });

  it("perfil complet i licitació sense metadades: la causa és la font", () => {
    const m = match(thinSource);
    expect(insufficientCause(m)).toBe("source");
    expect(sourceGaps(m)).toEqual(["cpv", "geography", "economic"]);
    expect(statusHelp(m)).toMatch(/La font no publica/);
    expect(shortSummary(m)).toBe("No hi ha prou metadades publicades per valorar-la");
  });

  it("causes barrejades: es diuen totes dues", () => {
    const m = match(mixed);
    expect(insufficientCause(m)).toBe("both");
    expect(profileGaps(m)).toEqual(["cpv"]);
    expect(sourceGaps(m)).toEqual(["economic", "timing"]);
    expect(statusHelp(m)).toMatch(/perfil/);
    expect(statusHelp(m)).toMatch(/font/);
  });

  it("un motiu desconegut no s'atribueix a ningú", () => {
    const m = match([factor("cpv", "unknown", "CODI_NOU_DEL_MOTOR")]);
    expect(insufficientCause(m)).toBe("unknown");
    expect(statusHelp(m)).not.toMatch(/La font no publica/);
    expect(statusHelp(m)).not.toMatch(/perfil de l'empresa encara/);
  });

  it("els factors avaluats no compten com a manca encara que portin un motiu de perfil", () => {
    // Només `unknown` és una manca: un factor amb resultat no és cap dada absent.
    const m = match([factor("cpv", "no_match", "CPV_NO_COMPANY_PREFERENCES")]);
    expect(profileGaps(m)).toEqual([]);
  });

  it("la resta d'estats conserven el seu text; el de llista no culpa ningú", () => {
    expect(statusHelp(match(emptyProfile, "candidate"))).toBe(MATCH_STATUS_HELP.candidate);
    expect(MATCH_STATUS_HELP.insufficient_data).not.toMatch(/^La font no publica/);
    expect(MATCH_STATUS_HELP.insufficient_data).toMatch(/perfil/);
  });
});

describe("F-06b (regles v2): base de l'import al factor econòmic", () => {
  const economic = (detail: Record<string, unknown>, source_value: string | null = "67.500") => ({
    dimension: "economic" as const,
    source_value,
    detail,
  });

  it("sense VEC, el PBL es diu i es marca com a mínim", () => {
    expect(factorSourceValue(economic({ amount_basis: "pbl_net", lower_bound: true }))).toBe(
      "67.500 € (PBL sense IVA, sense VEC publicat: com a mínim)",
    );
    expect(factorSourceValue(economic({ amount_basis: "pbl_gross" }))).toBe(
      "67.500 € (PBL amb IVA, sense VEC publicat: com a mínim)",
    );
  });

  it("amb VEC, es diu que és el VEC; un factor de v1 (sense base) es mostra com era", () => {
    expect(factorSourceValue(economic({ amount_basis: "vec" }, "245.079"))).toBe("245.079 € (VEC)");
    expect(factorSourceValue(economic({}, "245.079"))).toBe("245.079");
    expect(factorSourceValue(economic({ amount_basis: "pbl_net" }, null))).toBeNull();
    expect(factorSourceValue({ dimension: "cpv", source_value: "45314320", detail: { amount_basis: "pbl_net" } })).toBe("45314320");
  });

  it("els motius nous tenen text i compten com a dada de la font, no del perfil", () => {
    for (const code of ["AMOUNT_BUDGET_LOWER_BOUND", "AMOUNT_INCOHERENT"]) {
      expect(reasonLabel(code)).not.toBe(code);
      const m = match([factor("economic", "unknown", code), factor("cpv", "match", "CPV_EXACT_HIGH_PRIORITY")]);
      expect(sourceGaps(m)).toEqual(["economic"]);
      expect(profileGaps(m)).toEqual([]);
      expect(insufficientCause(m)).toBe("source");
    }
  });
});
