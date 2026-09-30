import { describe, expect, it } from "vitest";

import { claimFixture, emptySnapshotFixture, observationsFixture, snapshotFixture, vocabularyFixture } from "@/test/knowledge-fixture";

import {
  FACT_KEYS,
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
  describeProposal,
  entryState,
  findEntry,
  groupCandidates,
  groupPendingRecords,
  indexSnapshot,
  isPromotable,
  pendingCandidates,
  proposalHeadline,
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

  describe("què es desaria si es confirma: surt de la proposta, no del text de l'històric", () => {
    const fact = (key: string, value: boolean, detail?: string) =>
      describeProposal({ subject: "operating_fact", proposal: { key, value, ...(detail ? { detail } : {}) } }, vocabularyFixture);

    it("un fet operatiu en positiu es desa com a SÍ", () => {
      const described = describeProposal(byId["o-24x7"], vocabularyFixture);
      expect(described).toEqual({ label: "Suport 24x7", value: "SÍ", negative: false, fields: [], sentence: "Ofereix suport 24x7" });
      expect(proposalHeadline(described)).toBe("Suport 24x7: SÍ");
    });

    it("un fet operatiu en negatiu es desa com a NO, i es diu en negatiu", () => {
      const described = fact("own_ftth_infrastructure", false);
      expect(described).toEqual({
        label: "Infraestructura FTTH pròpia",
        value: "NO",
        negative: true,
        fields: [],
        sentence: "No disposa d'infraestructura FTTH pròpia",
      });
      expect(proposalHeadline(described)).toBe("Infraestructura FTTH pròpia: NO");
      // Res del que es mostra no es pot llegir com un sí.
      expect(`${proposalHeadline(described)} ${described.sentence}`).not.toMatch(/SÍ|Sí/);
      expect(described.sentence).toMatch(/^No /);
    });

    it("el mateix fet, en positiu i en negatiu, no es pot confondre", () => {
      for (const key of FACT_KEYS) {
        const yes = fact(key, true);
        const no = fact(key, false);
        expect([yes.value, yes.negative], key).toEqual(["SÍ", false]);
        expect([no.value, no.negative], key).toEqual(["NO", true]);
        expect(no.sentence, key).toMatch(/^No /);
        expect(yes.sentence, key).not.toMatch(/^No /);
        expect(yes.sentence, key).not.toBe(no.sentence);
      }
    });

    it("un fet que la pantalla no coneix no es queda sense valor", () => {
      expect(fact("fet_nou", false)).toMatchObject({ label: "fet_nou", value: "NO", negative: true, sentence: "fet_nou: NO" });
      expect(fact("fet_nou", true).sentence).toBe("fet_nou: SÍ");
    });

    it("l'aclariment d'un fet és un camp més del que es crearà", () => {
      expect(fact("datacenter_tier3", true, "Centre Demo").fields).toEqual([{ label: "Aclariment", value: "Centre Demo" }]);
    });

    it("una solució: el nom i els camps que es crearan", () => {
      expect(describeProposal(byId["o-sol"], vocabularyFixture)).toEqual({
        label: "Solució",
        value: "Centraleta Demo",
        negative: false,
        fields: [
          { label: "Línia de servei", value: "Centraleta i veu IP" },
          { label: "Fabricant", value: "Fabricant Demo" },
        ],
        sentence: "Solució Centraleta Demo · Centraleta i veu IP · fabricant Fabricant Demo",
      });
      expect(
        describeProposal({ subject: "solution", proposal: { name: "Centraleta Demo", service_line_code: "voice_pbx", edition: "Pro" } }, vocabularyFixture).fields,
      ).toEqual([
        { label: "Línia de servei", value: "Centraleta i veu IP" },
        { label: "Edició", value: "Pro" },
      ]);
    });

    it("un producte: fabricant, model i línia", () => {
      expect(
        describeProposal({ subject: "product", proposal: { manufacturer: "Marca Demo", model: "T1", service_line_code: "voice_pbx" } }, vocabularyFixture),
      ).toEqual({
        label: "Producte",
        value: "Marca Demo T1",
        negative: false,
        fields: [
          { label: "Fabricant", value: "Marca Demo" },
          { label: "Model", value: "T1" },
          { label: "Línia de servei", value: "Centraleta i veu IP" },
        ],
        sentence: "Producte Marca Demo T1 · Centraleta i veu IP",
      });
    });

    it("un partner: el nom i cada paper que es crearà", () => {
      const described = describeProposal(
        {
          subject: "partner",
          proposal: {
            name: "Soci Demo",
            roles: [{ role: "commercial_partner", territory: "Girona" }, { role: "subcontractor", service_line_code: "civil_works", territory: "Girona" }, { role: "bid_writer" }],
          },
        },
        vocabularyFixture,
      );
      expect(described).toEqual({
        label: "Partner",
        value: "Soci Demo",
        negative: false,
        fields: [
          { label: "Paper", value: "Soci comercial · Girona" },
          { label: "Paper", value: "Subcontractista · Obra civil · Girona" },
          { label: "Paper", value: "Redacció d'ofertes" },
        ],
        sentence: "Partner Soci Demo: Soci comercial · Girona; Subcontractista · Obra civil · Girona; Redacció d'ofertes",
      });
      expect(describeProposal({ subject: "partner", proposal: { name: "Soci Demo" } }, vocabularyFixture)).toMatchObject({ fields: [], sentence: "Partner Soci Demo" });
    });

    it("un servei: el nom, la línia i la modalitat", () => {
      expect(
        describeProposal({ subject: "service", proposal: { name: "Centraleta al núvol", service_line_code: "voice_pbx", delivery_mode: "resale" } }, vocabularyFixture),
      ).toEqual({
        label: "Servei",
        value: "Centraleta al núvol",
        negative: false,
        fields: [
          { label: "Línia de servei", value: "Centraleta i veu IP" },
          { label: "Modalitat", value: "Revenda" },
        ],
        sentence: "Servei «Centraleta al núvol» · Centraleta i veu IP · Revenda",
      });
    });

    it("una certificació que no es tenia es diu en negatiu", () => {
      expect(describeProposal(byId["o-iso"], vocabularyFixture)).toEqual({
        label: "ISO 9001",
        value: "NO",
        negative: true,
        fields: [],
        sentence: "No disposava de la certificació ISO 9001",
      });
      expect(describeProposal({ subject: "certification", proposal: { standard_code: "ENS", level: "medium" } }, vocabularyFixture)).toMatchObject({
        label: "Certificació",
        value: "ENS · Mitjà",
        negative: false,
        fields: [{ label: "Abast", value: "Sense estructurar" }],
      });
    });
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
    // La línia afegida a la 0046 té rètol propi, com les altres.
    expect(serviceLineLabel("mobile_telephony")).toBe("Telefonia mòbil");
    expect(serviceLineLabel("mobile_telephony", { ...vocabularyFixture, service_lines: [{ code: "mobile_telephony", label: "Telefonia mobil" }] })).toBe("Telefonia mòbil");
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
