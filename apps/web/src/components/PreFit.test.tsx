import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { prefitProfileReady, type MatchFactor, type TenderMatch } from "@/lib/matching";

import { PreFitMissing, PreFitPanel } from "./PreFit";

const counts = { cpv_preferences: 0, territories: 0, contract_preferences: 0 };

describe("pre-fit sense calcular (3B-2 §7.1.4)", () => {
  it("el perfil compta si té CPV, territoris o preferències de contracte", () => {
    expect(prefitProfileReady(null)).toBeNull();
    expect(prefitProfileReady(counts)).toBe(false);
    expect(prefitProfileReady({ ...counts, cpv_preferences: 2 })).toBe(true);
    expect(prefitProfileReady({ ...counts, territories: 1 })).toBe(true);
    expect(prefitProfileReady({ ...counts, contract_preferences: 1 })).toBe(true);
  });

  it("sense perfil: explica quan es calcula, no ofereix cap càlcul i no diu «per a totes»", () => {
    const onCalculate = vi.fn();
    const { container } = render(<PreFitMissing profileReady={false} calculating={false} error={null} onCalculate={onCalculate} />);
    expect(
      screen.getByText("El pre-fit només es calcula quan el perfil té CPV, territoris o preferències de contracte"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Calcula'l ara" })).toBeNull();
    expect(screen.getByRole("link", { name: /Preferències del Radar/ }).getAttribute("href")).toBe("/empresa/radar");
    expect(container.textContent).not.toMatch(/Completa el perfil/);
    expect(container.textContent).not.toMatch(/per a totes/);
  });

  it("amb perfil: «Encara no s'ha calculat» i el botó el calcula només per a aquesta licitació", () => {
    const onCalculate = vi.fn();
    const { container } = render(<PreFitMissing profileReady calculating={false} error={null} onCalculate={onCalculate} />);
    expect(screen.getByText("Encara no s'ha calculat")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Calcula'l ara" }));
    expect(onCalculate).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toMatch(/per a totes/);
  });

  it("un error del càlcul es diu sense perdre el botó", () => {
    render(<PreFitMissing profileReady calculating={false} error="No s'ha pogut calcular." onCalculate={() => undefined} />);
    expect(screen.getByText("No s'ha pogut calcular.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Calcula'l ara" })).toBeTruthy();
  });
});

/* ----- Causa de les dades insuficients (auditoria UI P1-02) ------------------ */

function panelMatch(factors: Array<[string, string, string]>): TenderMatch {
  return {
    id: "m1",
    tender_id: "t1",
    score: null,
    status: "insufficient_data",
    exclusion_kind: null,
    exclusion_reason_code: null,
    cpv_score: null,
    geographic_score: null,
    economic_score: null,
    timing_score: null,
    data_coverage: 0.15,
    factors: factors.map(([dimension, result, reason_code]) => ({
      dimension: dimension as MatchFactor["dimension"],
      result: result as MatchFactor["result"],
      reason_code,
      weight: 0.25,
      score: result === "unknown" ? null : 15,
      max_score: 25,
      company_value: null,
      source_value: null,
      detail: {},
    })),
    rules_version: "test",
    calculated_at: "2026-10-14T19:44:39Z",
    best_lot_id: null,
    candidate_lot_count: 0,
    total_lot_count: 0,
    is_stale: false,
    lot_matches: [],
  };
}

describe("PreFitPanel · de qui és la dada que falta", () => {
  it("perfil sense dades i licitació completa: no culpa la font i porta al perfil", () => {
    const { container } = render(
      <PreFitPanel
        match={panelMatch([
          ["cpv", "unknown", "CPV_NO_COMPANY_PREFERENCES"],
          ["geography", "unknown", "TERRITORY_NO_COMPANY_DATA"],
          ["economic", "unknown", "AMOUNT_NO_COMPANY_PREFERENCES"],
          ["timing", "match", "DEADLINE_ADEQUATE"],
        ])}
      />,
    );
    expect(container.textContent).not.toMatch(/La font no publica/);
    expect(container.textContent).toMatch(/El perfil de l'empresa encara no té prou dades/);
    const gap = container.querySelector("[data-gap='profile']");
    expect(gap?.textContent).toMatch(/Activitat \(CPV\), Territori, Import/);
    expect(screen.getByRole("link", { name: /Preferències del Radar/ }).getAttribute("href")).toBe("/empresa/radar");
    expect(container.querySelector("[data-gap='source']")).toBeNull();
  });

  it("licitació sense metadades: ho atribueix a la font, sense enllaç al perfil", () => {
    const { container } = render(
      <PreFitPanel
        match={panelMatch([
          ["cpv", "unknown", "CPV_UNKNOWN"],
          ["geography", "unknown", "TERRITORY_UNKNOWN"],
          ["economic", "match", "AMOUNT_PREFERRED"],
          ["timing", "unknown", "DEADLINE_UNKNOWN"],
        ])}
      />,
    );
    expect(container.textContent).toMatch(/La font no publica prou metadades/);
    expect(container.querySelector("[data-gap='source']")?.textContent).toMatch(/Activitat \(CPV\), Territori, Termini/);
    expect(container.querySelector("[data-gap='profile']")).toBeNull();
  });

  it("causes barrejades: les dues llistes, cadascuna amb el seu responsable", () => {
    const { container } = render(
      <PreFitPanel
        match={panelMatch([
          ["cpv", "unknown", "CPV_NO_COMPANY_PREFERENCES"],
          ["geography", "match", "TERRITORY_HIGH_PRIORITY"],
          ["economic", "unknown", "AMOUNT_UNKNOWN"],
          ["timing", "match", "DEADLINE_ADEQUATE"],
        ])}
      />,
    );
    expect(container.querySelector("[data-gap='profile']")?.textContent).toMatch(/Activitat \(CPV\)/);
    expect(container.querySelector("[data-gap='source']")?.textContent).toMatch(/Import/);
    expect(container.textContent).toMatch(/Falten dades al perfil de l'empresa i la font/);
  });
});

/* ----- Regles v3: mínim de CPV i termini desconegut que compta 0 ------------- */

function v3Match(overrides: Partial<TenderMatch>, factors: MatchFactor[]): TenderMatch {
  return { ...panelMatch([]), ...overrides, factors };
}

function v3Factor(
  dimension: MatchFactor["dimension"],
  result: MatchFactor["result"],
  reason_code: string,
  score: number | null,
  max_score: number,
  detail: Record<string, unknown> = {},
): MatchFactor {
  return { dimension, result, reason_code, weight: max_score, score, max_score, company_value: null, source_value: null, detail };
}

describe("PreFitPanel · regles v3", () => {
  it("el termini que no es publica es mostra com 0 de 10 i s'explica per què compta", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "candidate", score: 65, data_coverage: 0.9 }, [
          v3Factor("cpv", "match", "CPV_EXACT_MEDIUM_PRIORITY", 34, 40),
          v3Factor("geography", "partial", "TERRITORY_NOT_DECLARED", 6.25, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "unknown", "DEADLINE_UNKNOWN", 0, 10, { counts_as_zero: true }),
        ])}
      />,
    );
    expect(container.querySelector("[data-counts-as-zero]")?.textContent).toBe(": compta 0 de 10");
    expect(container.textContent).not.toMatch(/no avaluable/);
    const note = container.querySelector("[data-zeroed-note]");
    expect(note?.textContent).toMatch(/^Termini: la licitació no el publica i hi compta 0 punts/);
    // Cap altra dada falta: no hi ha el paràgraf de «no compta com a desencaix».
    expect(container.textContent).not.toMatch(/No compta com a desencaix/);
    expect(container.textContent).toMatch(/Dada no disponible/);
  });

  it("amb una altra dada desconeguda, la puntuació surt del pes avaluat més el termini", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "candidate", score: 87, data_coverage: 0.65 }, [
          v3Factor("cpv", "match", "CPV_EXACT_HIGH_PRIORITY", 40, 40),
          v3Factor("geography", "match", "TERRITORY_HIGH_PRIORITY", 25, 25),
          v3Factor("economic", "unknown", "AMOUNT_UNKNOWN", null, 25),
          v3Factor("timing", "unknown", "DEADLINE_UNKNOWN", 0, 10, { counts_as_zero: true }),
        ])}
      />,
    );
    expect(container.textContent).toMatch(/La puntuació surt del 75 % del pes, el termini inclòs amb 0 punts/);
    expect(container.textContent).toMatch(/Dades no disponibles/);
    expect(container.querySelector("[data-gap='source']")?.textContent).toMatch(/Import, Termini/);
  });

  it("un factor de termini de v2 (sense la marca) continua sent «no avaluable»", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "candidate", score: 72, data_coverage: 0.9 }, [
          v3Factor("cpv", "match", "CPV_EXACT_MEDIUM_PRIORITY", 34, 40),
          v3Factor("geography", "partial", "TERRITORY_NOT_DECLARED", 6.25, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "unknown", "DEADLINE_UNKNOWN", null, 10),
        ])}
      />,
    );
    expect(container.textContent).toMatch(/no avaluable/);
    expect(container.querySelector("[data-zeroed-note]")).toBeNull();
    expect(container.textContent).toMatch(/La puntuació surt del 90 % del pes que sí que s'ha pogut avaluar/);
  });

  it("sense puntuació, el termini que no es publica és una dada que falta com les altres", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "insufficient_data", score: null, data_coverage: 0.25 }, [
          v3Factor("cpv", "unknown", "CPV_UNKNOWN", null, 40),
          v3Factor("geography", "match", "TERRITORY_HIGH_PRIORITY", 25, 25),
          v3Factor("economic", "unknown", "AMOUNT_UNKNOWN", null, 25),
          v3Factor("timing", "unknown", "DEADLINE_UNKNOWN", 0, 10, { counts_as_zero: true }),
        ])}
      />,
    );
    expect(container.querySelector("[data-counts-as-zero]")).toBeNull();
    expect(container.querySelector("[data-zeroed-note]")).toBeNull();
    expect(container.textContent).toMatch(/Activitat \(CPV\), Import, Termini\. No compta com a desencaix/);
    expect(container.textContent).not.toMatch(/0 punts/);
  });

  it("un 72 sota el mínim de CPV diu per què no és candidata", () => {
    render(
      <PreFitPanel
        match={v3Match({ status: "low_fit", score: 72, data_coverage: 1 }, [
          v3Factor("cpv", "partial", "CPV_RELATED_CLASS", 18.7, 40, { below_candidate_minimum: true, candidate_minimum: 20 }),
          v3Factor("geography", "partial", "TERRITORY_MEDIUM_PRIORITY", 20, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "match", "DEADLINE_ADEQUATE", 8, 10),
        ])}
      />,
    );
    expect(screen.getByText(/no arriba al mínim de 20 de 40 punts per ser candidata/)).toBeTruthy();
  });
});

describe("PreFitPanel · regles v3, revisió", () => {
  it("sense cap CPV publicat, el CPV no «no compta com a desencaix»: és el motiu del baix encaix", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "low_fit", score: 100, data_coverage: 0.6 }, [
          v3Factor("cpv", "unknown", "CPV_UNKNOWN", null, 40, { below_candidate_minimum: true, candidate_minimum: 20 }),
          v3Factor("geography", "match", "TERRITORY_HIGH_PRIORITY", 25, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "match", "DEADLINE_ADEQUATE", 10, 10),
        ])}
      />,
    );
    expect(container.textContent).not.toMatch(/No compta com a desencaix/);
    expect(container.querySelector("[data-blocking-unknown]")?.textContent).toMatch(
      /^Activitat \(CPV\): sense aquesta dada no pot ser candidata/,
    );
    expect(container.textContent).toMatch(/La licitació no publica cap codi CPV: sense saber de quina activitat és/);
    expect(container.textContent).not.toMatch(/no arriba al mínim/);
    expect(container.textContent).toMatch(/La puntuació surt del 60 % del pes que sí que s'ha pogut avaluar/);
  });

  it("amb el CPV desconegut i una altra dada que falta, cadascuna amb la seva frase", () => {
    const { container } = render(
      <PreFitPanel
        // 25 + 25 de 60 (el termini hi compta amb 0): 83, com ho calcula el motor.
        match={v3Match({ status: "low_fit", score: 83, data_coverage: 0.5 }, [
          v3Factor("cpv", "unknown", "CPV_UNKNOWN", null, 40, { below_candidate_minimum: true, candidate_minimum: 20 }),
          v3Factor("geography", "match", "TERRITORY_HIGH_PRIORITY", 25, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "unknown", "DEADLINE_UNKNOWN", 0, 10, { counts_as_zero: true }),
        ])}
      />,
    );
    expect(container.querySelector("[data-blocking-unknown]")).not.toBeNull();
    expect(container.querySelector("[data-zeroed-note]")).not.toBeNull();
    expect(container.textContent).toMatch(/La puntuació surt del 60 % del pes, el termini inclòs amb 0 punts/);
  });

  it("un lot sense CPV propis diu amb quin CPV s'ha avaluat", () => {
    const { container } = render(
      <PreFitPanel
        match={v3Match({ status: "low_fit", score: 60, data_coverage: 1 }, [
          v3Factor("cpv", "no_match", "CPV_NO_OVERLAP", 0, 40, { inherited_from_tender: true }),
          v3Factor("geography", "match", "TERRITORY_HIGH_PRIORITY", 25, 25),
          v3Factor("economic", "match", "AMOUNT_PREFERRED", 25, 25),
          v3Factor("timing", "match", "DEADLINE_ADEQUATE", 10, 10),
        ])}
      />,
    );
    expect(container.querySelector("[data-inherited-cpv]")?.textContent).toBe(
      "Lot sense CPV propis: s'ha avaluat només amb el primer CPV que publica la font per a l'expedient.",
    );
  });
});
