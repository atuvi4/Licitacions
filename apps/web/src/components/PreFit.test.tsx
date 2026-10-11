import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { prefitProfileReady } from "@/lib/matching";

import { PreFitMissing } from "./PreFit";

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
