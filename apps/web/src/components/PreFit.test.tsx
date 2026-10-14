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
