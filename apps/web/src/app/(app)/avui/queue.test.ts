import { describe, expect, it } from "vitest";

import type { WorkspaceSummary } from "@/lib/workspace";

import {
  RADAR_NOTE,
  assignQueue,
  blockerReasons,
  candidatesCause,
  candidatesEmptyLine,
  candidatesLine,
  decisionsAboveLine,
  isOpenBlocker,
  isUnevaluated,
  needsPreparation,
  preparationAboveLine,
  todayLine,
  uncountedLine,
  withWorkspaceLine,
  type QueueAssignment,
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

const idsOf = (rows: { workspace: WorkspaceSummary }[] | null) => rows?.map((row) => row.workspace.id) ?? null;

describe("decisions pendents (assignQueue)", () => {
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
    expect(idsOf(assignQueue(rows, null).decisions)).toEqual(["urgent", "deferred", "far", "null-deadline"]);
  });

  it("un termini passat també és una decisió pendent si no s'ha decidit: va a bloquejos amb l'etiqueta", () => {
    const rows = [workspace({ id: "late", human_decision: null, days_remaining: -3, deadline_passed: true })];
    const queue = assignQueue(rows, null);
    expect(idsOf(queue.blockers)).toEqual(["late"]);
    expect(queue.blockers[0].tags.map((tag) => tag.label)).toEqual(["Decisió pendent"]);
    expect(queue.decisionsAbove).toBe(1);
  });
});

describe("bloquejos detectats (assignQueue) i blockerReasons", () => {
  it("només NO-GO, anàlisi endarrerida o termini passat, amb el motiu del diccionari", () => {
    const rows = [
      workspace({ id: "clean", eligibility: "eligible" }),
      workspace({ id: "nogo", eligibility: "no_go", days_remaining: 12 }),
      workspace({ id: "outdated", analysis_outdated: true, days_remaining: 4 }),
      workspace({ id: "late", deadline_passed: true, days_remaining: -2 }),
      workspace({ id: "nodeadline", eligibility: "eligible", days_remaining: null }),
    ];
    expect(idsOf(assignQueue(rows, null).blockers)).toEqual(["late", "outdated", "nogo"]);
    expect(blockerReasons(rows[1]).map((reason) => [reason.level, reason.label])).toEqual([["blocking", "No elegible"]]);
    expect(blockerReasons(rows[2]).map((reason) => [reason.level, reason.label])).toEqual([["attention", "Anàlisi endarrerida"]]);
    expect(blockerReasons(rows[3]).map((reason) => [reason.level, reason.label])).toEqual([["blocking", "Fora de termini"]]);
    expect(blockerReasons(rows[3])[0].detail).toBe("El termini de presentació ja ha passat.");
  });

  it("un termini null no és cap bloqueig", () => {
    expect(assignQueue([workspace({ id: "x", days_remaining: null, deadline_passed: false })], null).blockers).toEqual([]);
  });

  it("una candidatura tancada (presentada, retirada o NO-GO aprovat) no és mai un bloqueig, encara que el termini hagi vençut", () => {
    const closed = [
      workspace({ id: "submitted", status: "submitted", human_decision: "go", deadline_passed: true, days_remaining: -5 }),
      workspace({ id: "withdrawn", status: "withdrawn", human_decision: "go", deadline_passed: true, days_remaining: -1 }),
      workspace({ id: "nogo", status: "no_go_approved", human_decision: "no_go", eligibility: "no_go", deadline_passed: true }),
      workspace({ id: "outdated", status: "submitted", human_decision: "go", analysis_outdated: true }),
    ];
    expect(closed.map(isOpenBlocker)).toEqual([false, false, false, false]);
    const queue = assignQueue(closed, null);
    expect(queue.blockers).toEqual([]);
    expect(queue.decisions).toEqual([]);
  });

  it("una d'oberta amb el termini vençut continua sent un bloqueig", () => {
    const open = workspace({ id: "late", status: "preparing_bid", human_decision: "go", deadline_passed: true });
    expect(isOpenBlocker(open)).toBe(true);
  });
});

describe("todayLine", () => {
  const date = new Date(2026, 8, 16, 10, 0, 0); // dimecres 16 de setembre de 2026

  it("porta el dia, els recomptes i les candidates sobre el total de licitacions (A17)", () => {
    expect(todayLine(date, { decisions: 3, warnings: 5, candidates: 12, totalTenders: 17336 })).toBe(
      "Dimecres 16 de setembre · 3 decisions pendents, 5 bloquejos detectats, 12 candidates de 17.336 licitacions",
    );
    expect(todayLine(date, { decisions: 1, warnings: 1, candidates: 1, totalTenders: 1 })).toBe(
      "Dimecres 16 de setembre · 1 decisió pendent, 1 bloqueig detectat, 1 candidata de 1 licitació",
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

  it("regles v3: amb avaluacions i el perfil sense cap CPV, la causa és que no té CPV", () => {
    // Amb el mínim de CPV, cap licitació no pot ser candidata sense CPV, encara que hi hagi territoris.
    const noCpv = { cpv_preferences: 0, territories: 2, contract_preferences: 1 };
    expect(candidatesCause(radar(46), noCpv)).toBe("no_cpv");
    expect(candidatesEmptyLine(17336, 120, "no_cpv")).toBe("0 candidates de 17.336 licitacions: el perfil no té cap CPV");
    // El perfil buit continua sent `profile`.
    expect(candidatesCause(radar(46), EMPTY_PROFILE)).toBe("profile");
    // Sense cap avaluació, la causa continua sent que el Radar no s'ha calculat.
    expect(candidatesCause(radar(0), noCpv)).toBe("not_evaluated");
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
      "Dimecres 16 de setembre · 0 decisions pendents, 0 bloquejos detectats, Radar encara no calculat (46 licitacions)",
    );
    expect(todayLine(date, { decisions: 0, warnings: 0, candidates: 0, totalTenders: 46, evaluated: 46 })).toContain(
      "0 candidates de 46 licitacions",
    );
  });
});

describe("bloquejos, sense avaluar i pendents de preparació (auditoria UI P1-04)", () => {
  const unanalysed = workspace({ id: "lescala", status: "analysis", analysis_state: "absent", eligibility: null, human_decision: null });

  it("una candidatura sense anàlisi no és cap bloqueig detectat, però sí «sense avaluar»", () => {
    const queue = assignQueue([unanalysed], null);
    expect(queue.blockers).toEqual([]);
    expect(isUnevaluated(unanalysed)).toBe(true);
    expect(queue.unevaluatedTotal).toBe(1);
    // Ja decidida (GO), surt a «Sense avaluar»; sense decidir, a «Decisions pendents» amb l'etiqueta.
    const decided = { ...unanalysed, human_decision: "go" as const, status: "preparing_bid" as const };
    expect(idsOf(assignQueue([decided], null).unevaluated)).toEqual(["lescala"]);
    expect(queue.decisions[0].tags.map((tag) => tag.label)).toEqual(["Sense avaluar"]);
  });

  it("un NO-GO (no elegible) amb anàlisi és un bloqueig i no es barreja amb «sense avaluar»", () => {
    const noGo = workspace({ id: "nogo", eligibility: "no_go", analysis_state: "current" });
    expect(idsOf(assignQueue([noGo], null).blockers)).toEqual(["nogo"]);
    expect(isUnevaluated(noGo)).toBe(false);
  });

  it("les tancades i les fora de termini no compten com a sense avaluar", () => {
    expect(isUnevaluated({ ...unanalysed, status: "submitted" })).toBe(false);
    expect(isUnevaluated({ ...unanalysed, status: "no_go_approved" })).toBe(false);
    expect(isUnevaluated({ ...unanalysed, deadline_passed: true })).toBe(false);
    // Sense el camp (API antiga), no s'afirma res.
    expect(isUnevaluated({ ...unanalysed, analysis_state: undefined })).toBe(false);
  });

  it("pendents de preparació: les que en tenen, i les que no s'han pogut comptar; mai un zero inventat", () => {
    const ready = { human_decision: "go" as const, status: "preparing_bid" as const, analysis_state: "current" as const };
    const a = workspace({ id: "a", days_remaining: 3, ...ready });
    const b = workspace({ id: "b", days_remaining: 9, ...ready });
    const c = workspace({ id: "c", days_remaining: 12, ...ready });
    const rows = assignQueue([a, b, c], { a: 4, b: 0 }).preparation ?? [];
    expect(rows.map((row) => [row.workspace.id, row.pending])).toEqual([
      ["a", 4],
      ["c", null],
    ]);
  });

  it("needsPreparation: positiu o sense comptar; mai zero ni fora de l'abast", () => {
    expect(needsPreparation(4)).toBe(true);
    expect(needsPreparation(null)).toBe(true);
    expect(needsPreparation(0)).toBe(false);
    expect(needsPreparation(undefined)).toBe(false);
  });

  it("la línia del dia diu les sense avaluar a part, i no les compta com a bloquejos", () => {
    const line = todayLine(new Date(2026, 8, 16), {
      decisions: 1,
      warnings: 0,
      unevaluated: 1,
      candidates: 0,
      totalTenders: 17336,
      evaluated: 17336,
    });
    expect(line).toContain("0 bloquejos detectats, 1 candidatura sense avaluar");
    expect(line).not.toMatch(/avisos/);
  });
});

describe("assignQueue: cada candidatura una sola vegada (auditoria UI P2-05)", () => {
  const ids = (rows: { workspace: WorkspaceSummary }[] | null) => rows?.map((row) => row.workspace.id) ?? null;
  const tags = (assignment: QueueAssignment, id: string) =>
    [
      ...assignment.blockers,
      ...assignment.decisions,
      ...assignment.unevaluated,
      ...(assignment.preparation ?? []),
    ]
      .find((row) => row.workspace.id === id)
      ?.tags.map((tag) => tag.label);

  // L'Escala: oberta, sense decisió, sense anàlisi, 4 pendents de preparació.
  const lescala = workspace({ id: "lescala", status: "analysis", analysis_state: "absent", days_remaining: 1 });

  it("L'Escala va a «Decisions pendents» amb «Sense avaluar · 4 pendents», i enlloc més", () => {
    const queue = assignQueue([lescala], { lescala: 4 });
    expect(ids(queue.decisions)).toEqual(["lescala"]);
    expect(ids(queue.blockers)).toEqual([]);
    expect(ids(queue.unevaluated)).toEqual([]);
    expect(ids(queue.preparation)).toEqual([]);
    expect(tags(queue, "lescala")).toEqual(["Sense avaluar", "4 pendents de preparació (totes les àrees)"]);
    expect(queue.unevaluatedTotal).toBe(1);
    expect(queue.preparationAbove).toBe(1);
    expect(queue.uncountedAbove).toBe(0);
    expect(queue.decisionsAbove).toBe(0);
  });

  it("I-1: L'Escala sense comptar no compta com a «amb pendents», sinó a part", () => {
    const cases: Record<string, number | null>[] = [{ lescala: null }, {}];
    for (const counts of cases) {
      const queue = assignQueue([lescala], counts);
      expect(ids(queue.decisions)).toEqual(["lescala"]);
      expect(tags(queue, "lescala")).toEqual(["Sense avaluar", "Pendents sense comptar"]);
      expect(queue.preparationAbove).toBe(0);
      expect(queue.uncountedAbove).toBe(1);
    }
  });

  it("l'ordre de prioritat és bloqueig, decisió, sense avaluar, preparació; dins de cada secció, per termini", () => {
    const rows = [
      workspace({ id: "prep-late", human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: 9 }),
      workspace({ id: "blocker", eligibility: "no_go", analysis_state: "absent", days_remaining: 4 }),
      workspace({ id: "decision", analysis_state: "current", days_remaining: 5 }),
      workspace({ id: "unevaluated", human_decision: "go", status: "preparing_bid", analysis_state: "absent", days_remaining: 6 }),
      workspace({ id: "prep-soon", human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: 2 }),
      workspace({ id: "nothing", human_decision: "go", status: "preparing_bid", analysis_state: "current", days_remaining: 3 }),
      workspace({ id: "closed", human_decision: "go", status: "submitted", analysis_state: "absent", days_remaining: 1 }),
    ];
    const queue = assignQueue(rows, { "prep-late": 2, blocker: 1, decision: 0, unevaluated: 3, "prep-soon": null, nothing: 0 });
    expect(ids(queue.blockers)).toEqual(["blocker"]);
    expect(ids(queue.decisions)).toEqual(["decision"]);
    expect(ids(queue.unevaluated)).toEqual(["unevaluated"]);
    expect(ids(queue.preparation)).toEqual(["prep-soon", "prep-late"]);
    expect(tags(queue, "blocker")).toEqual(["Decisió pendent", "Sense avaluar", "1 pendent de preparació (totes les àrees)"]);
    expect(tags(queue, "decision")).toEqual([]);
    expect(tags(queue, "unevaluated")).toEqual(["3 pendents de preparació (totes les àrees)"]);
    // Una fila de preparació no porta cap etiqueta: no hi ha cap condició de prioritat inferior.
    expect(tags(queue, "prep-soon")).toEqual([]);
    expect(queue.preparation?.find((row) => row.workspace.id === "prep-soon")?.pending).toBeNull();
    expect(queue.decisionsAbove).toBe(1);
    expect(queue.preparationAbove).toBe(2);
    expect(queue.uncountedAbove).toBe(0);
    // Cap candidatura en dues seccions.
    const all = [...queue.blockers, ...queue.decisions, ...queue.unevaluated, ...(queue.preparation ?? [])].map((row) => row.workspace.id);
    expect(new Set(all).size).toBe(all.length);
  });

  it("una ajornada amb bloqueig porta «Ajornada · torna a decidir», com a «Decisions pendents»", () => {
    const deferred = workspace({ id: "d", human_decision: "defer", analysis_outdated: true, analysis_state: "current" });
    const queue = assignQueue([deferred], { d: 0 });
    expect(ids(queue.blockers)).toEqual(["d"]);
    expect(tags(queue, "d")).toEqual(["Ajornada · torna a decidir"]);
  });

  it("mentre es compten els pendents: cap fila només de preparació, cap etiqueta de preparació", () => {
    const rows = [
      lescala,
      workspace({ id: "prep", human_decision: "go", status: "preparing_bid", analysis_state: "current" }),
    ];
    const queue = assignQueue(rows, null);
    expect(queue.preparation).toBeNull();
    expect(ids(queue.decisions)).toEqual(["lescala"]);
    expect(tags(queue, "lescala")).toEqual(["Sense avaluar"]);
    expect(tags(queue, "prep")).toBeUndefined();
    expect(queue.preparationAbove).toBe(0);
  });

  it("sense comptar (`null`) o absent de la resposta: «Pendents sense comptar», mai un zero", () => {
    const queue = assignQueue([lescala], {});
    expect(tags(queue, "lescala")).toEqual(["Sense avaluar", "Pendents sense comptar"]);
    expect(queue.uncountedAbove).toBe(1);
    expect(queue.preparationAbove).toBe(0);
  });

  it("les fora de termini i les tancades no tenen pendents de preparació", () => {
    const late = workspace({ id: "late", human_decision: "go", status: "preparing_bid", deadline_passed: true, analysis_state: "current" });
    const queue = assignQueue([late], { late: 3 });
    expect(ids(queue.blockers)).toEqual(["late"]);
    expect(tags(queue, "late")).toEqual([]);
  });

  it("els textos de secció buida diuen on són les altres, en singular i en plural", () => {
    expect(decisionsAboveLine(1)).toBe("Cap altra decisió pendent: 1 amb un bloqueig detectat (a «Bloquejos i pendents»).");
    expect(decisionsAboveLine(3)).toBe("Cap altra decisió pendent: 3 amb un bloqueig detectat (a «Bloquejos i pendents»).");
    expect(preparationAboveLine(1)).toBe("Cap altre pendent de preparació: 1 candidatura amb pendents surt més amunt.");
    expect(preparationAboveLine(2)).toBe("Cap altre pendent de preparació: 2 candidatures amb pendents surten més amunt.");
    expect(uncountedLine(1)).toBe("No s'han pogut comptar els pendents d'una candidatura de més amunt.");
    expect(uncountedLine(3)).toBe("No s'han pogut comptar els pendents de 3 candidatures de més amunt.");
    expect(withWorkspaceLine(1)).toBe("1 candidata ja té candidatura");
    expect(withWorkspaceLine(4)).toBe("4 candidates ja tenen candidatura");
  });
});
