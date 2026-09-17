import { describe, expect, it } from "vitest";

import type { WorkspaceStatus } from "@/lib/workspace";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

import { primaryBanner, resolveStep, stepperItems } from "./steps";

describe("resolveStep", () => {
  it("el paràmetre de la URL mana si és un pas vàlid", () => {
    expect(resolveStep("tancar", "awaiting_decision")).toBe("tancar");
    expect(resolveStep("preparar", "no_go_approved")).toBe("preparar");
  });

  it("sense paràmetre (o amb un d'invàlid) el pas surt de l'estat real", () => {
    const table: Array<[WorkspaceStatus, string]> = [
      ["analysis", "decidir"],
      ["needs_review", "decidir"],
      ["awaiting_decision", "decidir"],
      ["go_approved", "preparar"],
      ["preparing_bid", "preparar"],
      ["ready_for_final_review", "tancar"],
      ["approved_for_submission", "tancar"],
      ["submitted", "tancar"],
      ["withdrawn", "tancar"],
      ["no_go_approved", "decidir"],
    ];
    for (const [status, step] of table) {
      expect(resolveStep(null, status), status).toBe(step);
      expect(resolveStep("foo", status), status).toBe(step);
    }
  });
});

describe("stepperItems", () => {
  it("sense decisió: Decidir en curs, Preparar s'obre amb GO, Tancar amb el que falta", () => {
    const items = stepperItems(workspaceFixture());
    expect(items.map((i) => [i.key, i.state])).toEqual([
      ["decidir", "on"],
      ["preparar", "todo"],
      ["tancar", "todo"],
    ]);
    expect(items[1].note).toBe("s'obre amb GO");
    expect(items[2].note).toBe("2 coses per resoldre");
    expect(items[0].pill).toBeNull();
  });

  it("amb GO: Decidir fet amb la decisió i la data, Preparar en curs", () => {
    const decision = decisionFixture();
    const items = stepperItems(
      workspaceFixture({
        status: "preparing_bid",
        human_decision: "go",
        latest_decision: decision,
        decisions: [decision],
        readiness: "ready_for_final_review",
        readiness_blockers: [],
      }),
    );
    expect(items.map((i) => i.state)).toEqual(["done", "on", "todo"]);
    expect(items[0].note).toContain("GO");
    expect(items[0].note).toContain("2026");
    expect(items[1].note).toBeNull();
    expect(items[2].note).toBe("dossier complet");
  });

  it("a Tancar: Preparar fet i Tancar en curs; presentada, tot fet", () => {
    const decision = decisionFixture();
    const base = { human_decision: "go" as const, latest_decision: decision, decisions: [decision] };
    expect(stepperItems(workspaceFixture({ ...base, status: "ready_for_final_review" })).map((i) => i.state)).toEqual(["done", "done", "on"]);
    expect(stepperItems(workspaceFixture({ ...base, status: "submitted" })).map((i) => i.state)).toEqual(["done", "done", "done"]);
  });

  it("decisió caducada: pastilla d'atenció al pas 1, sense amagar la decisió", () => {
    const decision = decisionFixture();
    const items = stepperItems(
      workspaceFixture({
        status: "go_approved",
        human_decision: "go",
        latest_decision: decision,
        decisions: [decision],
        decision_outdated: true,
        decision_reasons: ["tender_changed_since_decision"],
      }),
    );
    expect(items[0].state).toBe("done");
    expect(items[0].pill?.level).toBe("attention");
    expect(items[0].pill?.text).toContain("GO del");
    expect(items[0].pill?.text).toContain("el plec ha canviat");
  });

  it("NO-GO: Decidir fet amb NO-GO; els altres dos passos no s'obren", () => {
    const decision = decisionFixture({ decision: "no_go", reason: null });
    const items = stepperItems(
      workspaceFixture({ status: "no_go_approved", human_decision: "no_go", latest_decision: decision, decisions: [decision] }),
    );
    expect(items[0].state).toBe("done");
    expect(items[0].note).toContain("NO-GO");
    expect(items[1].state).toBe("todo");
    expect(items[1].note).toBe("tancada amb NO-GO");
  });

  it("ajornada: Decidir continua en curs i ho diu", () => {
    const decision = decisionFixture({ decision: "defer", reason: "Esperem la UTE." });
    const items = stepperItems(
      workspaceFixture({ status: "awaiting_decision", human_decision: "defer", latest_decision: decision, decisions: [decision] }),
    );
    expect(items[0].state).toBe("on");
    expect(items[0].pill).toEqual({ level: "attention", text: "Ajornada · torna a decidir" });
  });
});

describe("primaryBanner", () => {
  it("cap avís: cap bàner", () => {
    expect(primaryBanner(workspaceFixture())).toBeNull();
  });

  it("ordre de gravetat: anul·lada > lot retirat > termini > decisió > anàlisi > bloqueigs > avisos", () => {
    const everything = workspaceFixture({
      tender_actionable: false,
      lot_removed: true,
      deadline_passed: true,
      days_remaining: -3,
      decision_outdated: true,
      decision_reasons: ["tender_changed_since_decision"],
      changes_since_decision: [
        {
          change_code: "DEADLINE_CHANGED",
          subject: null,
          old_value: "2026-10-06",
          new_value: "2026-10-01",
          impacts: [],
          is_material: true,
          detected_at: "2026-09-13T00:00:00Z",
        },
      ],
      analysis_outdated: true,
      outdated_reasons: ["requirements_outdated"],
      counters: { ...workspaceFixture().counters, confirmed_blockers: 2 },
      open_warnings: ["confirmed_blockers", "deadline_passed", "outdated_analysis"],
    });
    const banner = primaryBanner(everything)!;
    expect(banner.level).toBe("blocking");
    expect(banner.kicker).toBe("Anul·lada");
    expect(banner.title).toContain("anul·lat");
    // La resta de motius viuen dins del mateix bàner, sense repetir el títol.
    expect(banner.reasons).toEqual(
      expect.arrayContaining([
        expect.stringContaining("lot"),
        expect.stringContaining("termini"),
        expect.stringContaining("plecs han canviat"),
        expect.stringContaining("bloqueig"),
      ]),
    );
    expect(banner.reasons.some((r) => r.includes("anul·lat"))).toBe(false);
    expect(new Set(banner.reasons).size).toBe(banner.reasons.length);
    expect(banner.action.href).toBe("/licitacions/t1");

    const noTender = { ...everything, tender_actionable: true };
    expect(primaryBanner(noTender)!.kicker).toBe("Lot retirat");
    const noLot = { ...noTender, lot_removed: false };
    const deadline = primaryBanner(noLot)!;
    expect(deadline.kicker).toBe("Termini");
    expect(deadline.level).toBe("blocking");
    expect(deadline.title).toBe("El termini de presentació ja ha passat");
    const noDeadline = {
      ...noLot,
      deadline_passed: false,
      days_remaining: 3,
      open_warnings: ["confirmed_blockers", "outdated_analysis"],
    };
    const decision = primaryBanner(noDeadline)!;
    expect(decision.kicker).toBe("Decisió");
    expect(decision.level).toBe("attention");
    expect(decision.title).toBe("La licitació ha canviat després de la decisió");
    expect(decision.reasons.some((r) => /termini/i.test(r))).toBe(true);
    const noDecision = { ...noDeadline, decision_outdated: false, decision_reasons: [], changes_since_decision: [] };
    const analysis = primaryBanner(noDecision)!;
    expect(analysis.kicker).toBe("Anàlisi");
    expect(analysis.level).toBe("attention");
    expect(analysis.reasons).toContain("Els plecs han canviat: cal tornar a analitzar-los.");
    expect(analysis.action.href).toBe("/licitacions/t1?tab=requisits");
    const noAnalysis = { ...noDecision, analysis_outdated: false, outdated_reasons: [], open_warnings: ["confirmed_blockers"] };
    const blockers = primaryBanner(noAnalysis)!;
    expect(blockers.kicker).toBe("Bloqueig");
    expect(blockers.level).toBe("blocking");
    expect(blockers.title).toBe("2 requisits obligatoris no es compleixen");
    expect(blockers.action.href).toBe("/licitacions/t1?tab=requisits");
    const onlyWarnings = { ...noAnalysis, counters: workspaceFixture().counters, open_warnings: ["mandatory_unknown"] };
    const warnings = primaryBanner(onlyWarnings)!;
    expect(warnings.kicker).toBe("Avisos");
    expect(warnings.level).toBe("attention");
    expect(warnings.title).toBe(
      "Hi ha requisits obligatoris sense dades al perfil: no se'n pot dir ni que sí ni que no.",
    );
  });

  it("decisió sense base coneguda: el títol ho diu i el motiu és el del diccionari", () => {
    const banner = primaryBanner(
      workspaceFixture({ decision_outdated: true, decision_basis_unknown: true, decision_reasons: ["revision_basis_unknown"] }),
    )!;
    expect(banner.title).toBe("Cal revisar la decisió");
    expect(banner.reasons[0]).toContain("anterior a l'historial de canvis");
  });

  it("un sol bloqueig confirmat es diu en singular", () => {
    const banner = primaryBanner(
      workspaceFixture({ counters: { ...workspaceFixture().counters, confirmed_blockers: 1 }, open_warnings: ["confirmed_blockers"] }),
    )!;
    expect(banner.title).toBe("1 requisit obligatori no es compleix");
  });
});
