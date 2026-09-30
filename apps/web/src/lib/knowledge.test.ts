import { describe, expect, it } from "vitest";

import { claimFixture, emptySnapshotFixture, observationsFixture, snapshotFixture, vocabularyFixture } from "@/test/knowledge-fixture";

import {
  FLAG_LABELS,
  STATE_LABELS,
  canDecideKnowledge,
  canDeleteKnowledge,
  canEditKnowledge,
  canVerifyKnowledge,
  candidateTitle,
  capabilityValue,
  certificationTitle,
  claimState,
  entryState,
  findEntry,
  groupCandidates,
  groupPendingRecords,
  indexSnapshot,
  isPromotable,
  pendingCandidates,
  proposalSummary,
  recordState,
  serviceLineLabel,
  summarize,
} from "./knowledge";

describe("estat efectiu del coneixement", () => {
  it("verificat només si la fotografia ho diu; si no, declarat amb el que hi falta", () => {
    expect(claimState(claimFixture({ source: "current_verified", has_evidence: true }))).toEqual({ state: "verified", flags: [] });
    expect(claimState(claimFixture())).toEqual({ state: "declared", flags: ["evidence_missing"] });
    expect(claimState(claimFixture({ has_evidence: true }))).toEqual({ state: "declared", flags: ["to_verify"] });
    expect(claimState(claimFixture({ scope_status: "scope_unknown" }))).toEqual({ state: "declared", flags: ["evidence_missing", "scope_unknown"] });
    expect(claimState(claimFixture({ source: "current_verified", has_evidence: true, scope_status: "scope_unknown" }))).toEqual({
      state: "verified",
      flags: ["scope_unknown"],
    });
  });

  it("una fila pren l'estat de la fotografia, no el seu propi camp de verificació", () => {
    const index = indexSnapshot(snapshotFixture());
    // cert-ens és `verified` a la base, però la fotografia la dona per declarada.
    expect(recordState("cert-ens", "current", index)).toEqual({ state: "declared", flags: ["evidence_missing", "scope_unknown"] });
    expect(recordState("fact-tier3", "current", index).state).toBe("verified");
    expect(recordState("partner-1", "current", index)).toEqual({ state: "declared", flags: ["to_verify"] });
  });

  it("el que la fotografia no compta és caducat, en tràmit, retirat o no compta", () => {
    const snapshot = snapshotFixture();
    snapshot.pending_validation.push(
      { kind: "expired", subject: "certification", key: "ISO_9001", detail: "Caducat el 2026-01-01", record_id: "cert-old", observation_id: null, superseded_by_current: false },
      { kind: "in_progress", subject: "certification", key: "ISO_27001", detail: "En tramit", record_id: "cert-wip", observation_id: null, superseded_by_current: false },
    );
    const index = indexSnapshot(snapshot);
    expect(recordState("cert-old", "current", index).state).toBe("expired");
    expect(recordState("cert-wip", "in_progress", index).state).toBe("in_progress");
    expect(recordState("fora", "retired", index).state).toBe("retired");
    expect(recordState("fora", "current", index).state).toBe("inactive");
    expect(recordState("x", "current", indexSnapshot(null)).state).toBe("inactive");
  });

  it("una clau sense dada actual és històrica si en parla l'històric, i desconeguda si no", () => {
    const snapshot = snapshotFixture();
    expect(entryState(findEntry(snapshot, "facts", "support_24x7")).state).toBe("historic");
    expect(entryState(findEntry(snapshot, "facts", "own_number_range_nrn")).state).toBe("unknown");
    expect(entryState(findEntry(snapshot, "certifications", "ENS")).state).toBe("declared");
  });

  it("cap rètol és un codi intern", () => {
    for (const label of [...Object.values(STATE_LABELS), ...Object.values(FLAG_LABELS)]) {
      expect(label).not.toMatch(/_|current|verified|declared|scope|unknown/);
    }
  });
});

describe("pendents i resum", () => {
  it("agrupa els pendents per dada i deixa a part els papers d'un partner", () => {
    const groups = groupPendingRecords(snapshotFixture());
    expect(groups.map((group) => [group.subject, group.key, group.kinds])).toEqual([
      ["certification", "ENS", ["accreditation_pending", "scope_unknown"]],
      ["partner", "Soci Demo", ["accreditation_pending"]],
    ]);
    expect(pendingCandidates(snapshotFixture()).map((item) => item.observation_id)).toEqual(["o-ens", "o-iso", "o-24x7", "o-sol"]);
  });

  it("el resum compta afirmacions, feina pendent i el que no se sap de les llistes tancades", () => {
    expect(summarize(snapshotFixture(), vocabularyFixture)).toEqual({ verified: 1, declared: 2, pending: 2, historic: 4, unknown: 6 });
    // Empresa nova: res de res, i tot el vocabulari tancat és desconegut (2 normes + 5 fets + 3 línies).
    expect(summarize(emptySnapshotFixture(), vocabularyFixture)).toEqual({ verified: 0, declared: 0, pending: 0, historic: 0, unknown: 10 });
    expect(summarize(null, vocabularyFixture).unknown).toBe(0);
  });
});

describe("candidats de l'històric", () => {
  const byId = Object.fromEntries(observationsFixture.map((item) => [item.id, item]));

  it("una observació negativa no es pot confirmar com a actual", () => {
    expect(isPromotable(byId["o-iso"])).toBe(false);
    expect(isPromotable(byId["o-24x7"])).toBe(true);
    expect(isPromotable(byId["o-sol"])).toBe(true);
  });

  it("diu què naixeria si es confirma, amb paraules", () => {
    expect(proposalSummary(byId["o-24x7"], vocabularyFixture)).toBe("Suport 24x7: Sí");
    expect(proposalSummary(byId["o-sol"], vocabularyFixture)).toBe("Centraleta Demo · Centraleta i veu IP · Fabricant Demo");
    expect(proposalSummary(byId["o-iso"], vocabularyFixture)).toBe("L'històric diu que NO es tenia ISO 9001");
    expect(
      proposalSummary({ subject: "partner", proposal: { name: "Soci Demo", roles: [{ role: "wholesale_operator" }] } }, vocabularyFixture),
    ).toBe("Soci Demo · Operador majorista");
  });

  it("agrupa per categoria i, si es demana, deixa al final el que només es pot descartar", () => {
    const superseded = new Set(["o-ens"]);
    expect(groupCandidates(observationsFixture, superseded).map((group) => [group.label, group.items.length])).toEqual([
      ["Certificacions", 2],
      ["Fets operatius", 1],
      ["Solucions", 1],
    ]);
    expect(groupCandidates(observationsFixture, superseded, true).map((group) => [group.label, group.items.map((item) => item.id)])).toEqual([
      ["Fets operatius", ["o-24x7"]],
      ["Solucions", ["o-sol"]],
      ["Només es poden descartar", ["o-ens", "o-iso"]],
    ]);
  });

  it("titula cada candidat amb el nom que veu la persona", () => {
    expect(candidateTitle("operating_fact", "telecom_operator_cnmc")).toBe("Operador inscrit a la CNMC");
    expect(candidateTitle("certification", "ENS", vocabularyFixture)).toBe("ENS");
    expect(candidateTitle("service", "voice_pbx", vocabularyFixture)).toBe("Centraleta i veu IP");
    expect(candidateTitle("solution", "Centraleta Demo")).toBe("Centraleta Demo");
  });
});

describe("rètols i permisos", () => {
  it("posa accents als rètols del vocabulari i cau al de l'API si no el coneix", () => {
    expect(serviceLineLabel("radio_link")).toBe("Radioenllaços");
    expect(serviceLineLabel("linia_nova", { ...vocabularyFixture, service_lines: [{ code: "linia_nova", label: "Linia nova" }] })).toBe("Linia nova");
    expect(certificationTitle({ name: "x", standard_code: "ENS", level: "medium" }, vocabularyFixture)).toBe("ENS · Mitjà");
    expect(certificationTitle({ name: "Certificat propi", standard_code: null })).toBe("Certificat propi");
  });

  it("formata el valor d'una capacitat sense zeros de la base", () => {
    expect(capabilityValue({ capability_code: "pbx.concurrent_calls", value_number: "60.0000", value_level: null }, vocabularyFixture)).toBe("60 canals");
    expect(capabilityValue({ capability_code: "switch.poe_standard", value_number: null, value_level: "at" }, vocabularyFixture)).toBe("at");
    expect(capabilityValue({ capability_code: "pbx.queues", value_number: null, value_level: null }, vocabularyFixture)).toBe("");
  });

  it("qui només consulta no edita, i eliminar és de propietari i administració", () => {
    const roles = ["owner", "admin", "bid_manager", "technical", "viewer", null];
    expect(roles.map(canEditKnowledge)).toEqual([true, true, true, true, false, false]);
    expect(roles.map(canDeleteKnowledge)).toEqual([true, true, false, false, false, false]);
  });

  it("confirmar l'històric i verificar segueixen els rols de l'API", () => {
    expect(["owner", "admin", "bid_manager", "technical", "viewer"].map(canDecideKnowledge)).toEqual([true, true, true, false, false]);
    expect(["owner", "admin", "bid_manager", "technical", "viewer"].map(canVerifyKnowledge)).toEqual([true, true, false, false, false]);
  });
});
