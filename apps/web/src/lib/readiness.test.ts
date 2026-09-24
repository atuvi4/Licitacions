import { describe, expect, it } from "vitest";

import { findingFixture } from "@/test/readiness-fixture";
import {
  FINDING_COPY,
  PREPARE_AREAS,
  blockerCount,
  findingCopy,
  groupFindings,
  isPrepareArea,
} from "@/lib/readiness";

describe("Àrees de Preparar", () => {
  it("n'hi ha quatre i cap vista de resum", () => {
    expect([...PREPARE_AREAS]).toEqual(["solucio", "oferta", "dossier", "pla"]);
    expect(PREPARE_AREAS).not.toContain("visio");
    expect(PREPARE_AREAS).not.toContain("resum");
  });

  it("una àrea desconeguda no passa per bona", () => {
    expect(isPrepareArea("oferta")).toBe(true);
    expect(isPrepareArea("visio-general")).toBe(false);
    expect(isPrepareArea(null)).toBe(false);
    expect(isPrepareArea(undefined)).toBe(false);
  });
});

describe("Recompte de bloquejos", () => {
  it("només compta els bloquejos", () => {
    const findings = [
      findingFixture({ severity: "blocker" }),
      findingFixture({ severity: "blocker", code: "mandatory_evidence_missing" }),
      findingFixture({ severity: "warning", code: "technical_solution_requirement_missing" }),
      findingFixture({ severity: "info", code: "economic_unit_limit_unknown" }),
    ];
    expect(blockerCount(findings)).toBe(2);
  });

  it("cap problema vol dir cap bloqueig", () => {
    expect(blockerCount([])).toBe(0);
  });
});

describe("Agrupació de problemes", () => {
  it("els grups són els tres nivells de gravetat, en ordre", () => {
    const groups = groupFindings([
      findingFixture({ domain: "dossier", severity: "info", code: "evidence_needs_review" }),
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_offer_price_missing" }),
      findingFixture({ domain: "technical", severity: "warning", code: "technical_scope_stale" }),
    ]);
    expect(groups.map((group) => group.severity)).toEqual(["blocker", "warning", "info"]);
    expect(groups.map((group) => group.label)).toEqual([
      "Bloquegen la presentació",
      "Avisos",
      "Per informació",
    ]);
  });

  it("dins d'un nivell, els dominis van sempre en el mateix ordre", () => {
    const groups = groupFindings([
      findingFixture({ domain: "workspace", severity: "blocker", code: "decision_not_go" }),
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_lot_empty" }),
      findingFixture({ domain: "dossier", severity: "blocker", code: "mandatory_evidence_missing" }),
      findingFixture({ domain: "technical", severity: "blocker", code: "technical_scope_unreviewed" }),
    ]);
    expect(groups[0].findings.map((finding) => finding.domain)).toEqual([
      "dossier",
      "technical",
      "economic",
      "workspace",
    ]);
  });

  it("l'ordre intern no depèn de quin domini porta més problemes", () => {
    const moltEconomic = groupFindings([
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_lot_empty" }),
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_offer_price_missing", entity_id: "l2" }),
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_vat_missing", entity_id: "l3" }),
      findingFixture({ domain: "dossier", severity: "blocker", code: "mandatory_evidence_missing" }),
    ]);
    // El dossier va davant encara que l'oferta en porti tres.
    expect(moltEconomic[0].findings[0].domain).toBe("dossier");
  });

  it("resoldre un bloqueig no mou de lloc el que queda", () => {
    const abans = groupFindings([
      findingFixture({ domain: "technical", severity: "blocker", code: "technical_scope_unreviewed" }),
      findingFixture({ domain: "dossier", severity: "warning", code: "evidence_needs_review" }),
      findingFixture({ domain: "economic", severity: "warning", code: "economic_lot_budget_unknown" }),
    ]);
    expect(abans.map((group) => group.severity)).toEqual(["blocker", "warning"]);
    expect(abans[1].findings.map((finding) => finding.domain)).toEqual(["dossier", "economic"]);

    // Cau l'únic bloqueig: el grup desapareix i els avisos no es remenen.
    const despres = groupFindings([
      findingFixture({ domain: "dossier", severity: "warning", code: "evidence_needs_review" }),
      findingFixture({ domain: "economic", severity: "warning", code: "economic_lot_budget_unknown" }),
    ]);
    expect(despres.map((group) => group.severity)).toEqual(["warning"]);
    expect(despres[0].findings.map((finding) => finding.domain)).toEqual(["dossier", "economic"]);
  });

  it("no inventa nivells buits", () => {
    expect(groupFindings([])).toEqual([]);
    const groups = groupFindings([findingFixture({ severity: "warning", code: "evidence_needs_review" })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].severity).toBe("warning");
  });
});

describe("Codis → text i destinació", () => {
  it("un problema tècnic porta a la solució tècnica", () => {
    expect(findingCopy("technical_scope_unreviewed")?.destination).toEqual({ kind: "area", area: "solucio" });
    expect(findingCopy("technical_solution_unvalidated")?.destination).toEqual({ kind: "area", area: "solucio" });
  });

  it("un problema econòmic porta a l'oferta econòmica", () => {
    expect(findingCopy("economic_offer_price_missing")?.destination).toEqual({ kind: "area", area: "oferta" });
    expect(findingCopy("economic_lot_empty")?.destination).toEqual({ kind: "area", area: "oferta" });
  });

  it("un problema del dossier porta al dossier, i la feina pendent al pla de treball", () => {
    expect(findingCopy("mandatory_evidence_missing")?.destination).toEqual({ kind: "area", area: "dossier" });
    expect(findingCopy("mandatory_tasks_open")?.destination).toEqual({ kind: "area", area: "pla" });
    expect(findingCopy("mandatory_checklist_blocked")?.destination).toEqual({ kind: "area", area: "pla" });
  });

  it("els plecs es resolen als plecs, no dins de Preparar", () => {
    expect(findingCopy("tender_documents_unreviewed")?.destination).toEqual({
      kind: "tender",
      tab: "plecs",
    });
    expect(findingCopy("tender_documents_changed")?.destination).toEqual({
      kind: "tender",
      tab: "plecs",
    });
  });

  it("que l'anàlisi no cobreixi els plecs es resol on viu l'anàlisi", () => {
    // Portar-ho a Decidir enviaria algú a un lloc on no hi ha el botó que
    // necessita: tornar a executar l'anàlisi es fa a la pestanya de requisits.
    expect(findingCopy("requirement_analysis_predates_documents")?.destination).toEqual({
      kind: "tender",
      tab: "requisits",
    });
  });

  it("cap dels tres findings de plecs no diu que els requisits hagin canviat", () => {
    for (const code of [
      "tender_documents_unreviewed",
      "tender_documents_changed",
      "requirement_analysis_predates_documents",
    ]) {
      const copy = findingCopy(code);
      const text = `${copy?.label} ${copy?.hint ?? ""}`.toLowerCase();
      expect(text).not.toMatch(/requisits han canviat/);
    }
  });

  it("el que no es resol preparant res porta a Decidir", () => {
    expect(findingCopy("decision_not_go")?.destination).toEqual({ kind: "step", step: "decidir" });
    expect(findingCopy("analysis_outdated")?.destination).toEqual({ kind: "step", step: "decidir" });
  });

  it("l'abast obsolet i una validació caducada són coses diferents", () => {
    const scope = findingCopy("technical_scope_stale");
    const validation = findingCopy("technical_solution_validation_stale");
    expect(scope).toBeTruthy();
    expect(validation).toBeTruthy();
    expect(scope!.label).not.toBe(validation!.label);
  });

  it("un codi desconegut no té text: la interfície ho ha de dir, no amagar-ho", () => {
    expect(findingCopy("un_codi_que_encara_no_existeix")).toBeNull();
  });

  it("cap text no decideix la gravetat: la posa el servidor", () => {
    for (const copy of Object.values(FINDING_COPY)) {
      expect(copy).not.toHaveProperty("severity");
    }
  });

  it("no hi ha cap codi per a «requisit sense solució»: això no és cap problema per si sol", () => {
    const codes = Object.keys(FINDING_COPY);
    expect(codes).not.toContain("requirement_without_solution");
    expect(codes.filter((code) => /requirement_(missing|without)/.test(code))).toEqual([
      "technical_solution_requirement_missing",
    ]);
  });
});
