import { describe, expect, it } from "vitest";

import type { WorkspaceSummary } from "@/lib/workspace";

import {
  RADAR_NOTE,
  blockerReasons,
  candidatesCause,
  candidatesEmptyLine,
  candidatesLine,
  openBlockers,
  pendingDecisions,
  todayLine,
} from "./queue";

function workspace(overrides: Partial<WorkspaceSummary>): WorkspaceSummary {
  return {
    id: "w",
    company_id: "c",
    tender_id: "t",
    tender_lot_id: null,
    status: "awaiting_decision",
    tender_title: "Licitació",
    expediente_number: null,
    buyer_name: null,
    deadline_at: null,
    days_remaining: null,
    deadline_passed: false,
    prefit_score: null,
    eligibility: null,
    recommendation: null,
    human_decision: null,
    analysis_basis: "real",
    analysis_outdated: false,
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

describe("pendingDecisions", () => {
  it("inclou sense decisió i ajornades, exclou tancades, i ordena per termini amb null al final", () => {
    const rows = [
      workspace({ id: "null-deadline", human_decision: null, days_remaining: null }),
      workspace({ id: "far", human_decision: null, days_remaining: 31 }),
      workspace({ id: "deferred", human_decision: "defer", days_remaining: 18 }),
      workspace({ id: "urgent", human_decision: null, days_remaining: 6 }),
      workspace({ id: "go", human_decision: "go", status: "preparing_bid", days_remaining: 2 }),
      workspace({ id: "submitted", human_decision: null, status: "submitted", days_remaining: 1 }),
      workspace({ id: "withdrawn", human_decision: null, status: "withdrawn", days_remaining: 1 }),
      workspace({ id: "nogo", human_decision: "no_go", status: "no_go_approved", days_remaining: 1 }),
    ];
    expect(pendingDecisions(rows).map((row) => row.id)).toEqual(["urgent", "deferred", "far", "null-deadline"]);
  });

  it("un termini passat també és una decisió pendent si no s'ha decidit", () => {
    const rows = [workspace({ id: "late", human_decision: null, days_remaining: -3, deadline_passed: true })];
    expect(pendingDecisions(rows).map((row) => row.id)).toEqual(["late"]);
  });
});

describe("openBlockers i blockerReasons", () => {
  it("només NO-GO, anàlisi endarrerida o termini passat, amb el motiu del diccionari", () => {
    const rows = [
      workspace({ id: "clean", eligibility: "eligible" }),
      workspace({ id: "nogo", eligibility: "no_go", days_remaining: 12 }),
      workspace({ id: "outdated", analysis_outdated: true, days_remaining: 4 }),
      workspace({ id: "late", deadline_passed: true, days_remaining: -2 }),
      workspace({ id: "nodeadline", eligibility: "eligible", days_remaining: null }),
    ];
    expect(openBlockers(rows).map((row) => row.id)).toEqual(["late", "outdated", "nogo"]);
    expect(blockerReasons(rows[1]).map((reason) => [reason.level, reason.label])).toEqual([["blocking", "No elegible"]]);
    expect(blockerReasons(rows[2]).map((reason) => [reason.level, reason.label])).toEqual([["attention", "Anàlisi endarrerida"]]);
    expect(blockerReasons(rows[3]).map((reason) => [reason.level, reason.label])).toEqual([["blocking", "Fora de termini"]]);
    expect(blockerReasons(rows[3])[0].detail).toBe("El termini de presentació ja ha passat.");
  });

  it("un termini null no és cap bloqueig", () => {
    expect(openBlockers([workspace({ id: "x", days_remaining: null, deadline_passed: false })])).toEqual([]);
  });
});

describe("todayLine", () => {
  const date = new Date(2026, 8, 16, 10, 0, 0); // dimecres 16 de setembre de 2026

  it("porta el dia, els recomptes i les candidates sobre el total de licitacions (A17)", () => {
    expect(todayLine(date, { decisions: 3, warnings: 5, candidates: 12, totalTenders: 17336 })).toBe(
      "Dimecres 16 de setembre · 3 decisions pendents, 5 avisos, 12 candidates de 17.336 licitacions",
    );
    expect(todayLine(date, { decisions: 1, warnings: 1, candidates: 1, totalTenders: 1 })).toBe(
      "Dimecres 16 de setembre · 1 decisió pendent, 1 avís, 1 candidata de 1 licitació",
    );
    expect(todayLine(date, { decisions: 0, warnings: 0, candidates: 0, totalTenders: 17336 })).toContain(
      "0 candidates de 17.336 licitacions",
    );
    expect(todayLine(date, { decisions: 0, warnings: 0, candidates: 0, totalTenders: 17336 })).not.toMatch(/oportunitats noves/);
  });

  it("mentre falta alguna crida no diu cap recompte, i mai un zero provisional", () => {
    expect(todayLine(date, { decisions: 3, warnings: undefined, candidates: 12, totalTenders: 148 })).toBe(
      "Dimecres 16 de setembre · comptant la feina pendent…",
    );
    expect(todayLine(date, { decisions: 3, warnings: 2, candidates: 12, totalTenders: undefined })).toBe(
      "Dimecres 16 de setembre · comptant la feina pendent…",
    );
  });
});

describe("candidates del Radar (A17: prioritzades pel Radar, no licitacions ingerides)", () => {
  it("«N candidates de M licitacions», amb el total en format català", () => {
    expect(candidatesLine(0, 17336)).toBe("0 candidates de 17.336 licitacions");
    expect(candidatesLine(1, 2)).toBe("1 candidata de 2 licitacions");
    expect(candidatesLine(3, 1)).toBe("3 candidates de 1 licitació");
  });

  const EMPTY_PROFILE = { cpv_preferences: 0, territories: 0, contract_preferences: 0 };
  const FULL_PROFILE = { cpv_preferences: 4, territories: 2, contract_preferences: 1 };
  const radar = (evaluated: number, candidates = 0, insufficient = 0) => ({
    evaluated_count: evaluated,
    candidates,
    insufficient_data: insufficient,
  });

  it("F1: sense cap avaluació la causa és «no calculat», tant si el perfil és complet com si és buit", () => {
    expect(candidatesCause(radar(0), FULL_PROFILE)).toBe("not_evaluated");
    expect(candidatesCause(radar(0), EMPTY_PROFILE)).toBe("not_evaluated");
    expect(candidatesCause(radar(0), null)).toBe("not_evaluated");
  });

  it("amb avaluacions: el perfil buit, les dades insuficients o cap coincidència, mai deduït només del perfil", () => {
    expect(candidatesCause(radar(46), FULL_PROFILE)).toBe("no_match");
    expect(candidatesCause(radar(46, 0, 10), FULL_PROFILE)).toBe("no_match");
    expect(candidatesCause(radar(46, 0, 46), FULL_PROFILE)).toBe("insufficient_data");
    expect(candidatesCause(radar(46), EMPTY_PROFILE)).toBe("profile");
    // Amb candidates no hi ha cap 0 a explicar; sense resum del Radar no s'afirma res.
    expect(candidatesCause(radar(46, 3), FULL_PROFILE)).toBeNull();
    expect(candidatesCause(null, FULL_PROFILE)).toBeNull();
    expect(candidatesCause(undefined, EMPTY_PROFILE)).toBeNull();
  });

  it("el buit explica el 0 amb la causa, amb les paraules exactes", () => {
    expect(candidatesEmptyLine(46, 0, "not_evaluated")).toBe("0 candidates de 46 licitacions: el Radar encara no s'ha calculat");
    expect(candidatesEmptyLine(17336, 17336, "profile")).toBe("0 candidates de 17.336 licitacions: el perfil no té CPV/territoris");
    expect(candidatesEmptyLine(17336, 120, "no_match")).toBe("0 candidates de 17.336 licitacions: cap coincidència entre les 120 avaluades");
    expect(candidatesEmptyLine(46, 1, "no_match")).toBe("0 candidates de 46 licitacions: cap coincidència entre l'única avaluada");
    expect(candidatesEmptyLine(46, 46, "insufficient_data")).toBe("0 candidates de 46 licitacions: dades insuficients a les 46 avaluades");
    // Sense causa coneguda no s'afirma res.
    expect(candidatesEmptyLine(17336, 0, null)).toBe("0 candidates de 17.336 licitacions");
    expect(RADAR_NOTE).toBe("candidates prioritzades pel Radar, no licitacions ingerides");
  });

  it("F1: «no calculat» mai diu «cap coincidència» ni culpa el perfil", () => {
    const line = candidatesEmptyLine(46, 0, candidatesCause(radar(0), FULL_PROFILE));
    expect(line).not.toMatch(/cap coincidència|perfil|CPV|territoris|amplia/);
  });

  it("la línia del dia no compta candidates mentre el Radar no s'ha calculat", () => {
    const date = new Date(2026, 8, 16, 10, 0, 0);
    expect(todayLine(date, { decisions: 0, warnings: 0, candidates: 0, totalTenders: 46, evaluated: 0 })).toBe(
      "Dimecres 16 de setembre · 0 decisions pendents, 0 avisos, Radar encara no calculat (46 licitacions)",
    );
    expect(todayLine(date, { decisions: 0, warnings: 0, candidates: 0, totalTenders: 46, evaluated: 46 })).toContain(
      "0 candidates de 46 licitacions",
    );
  });
});
