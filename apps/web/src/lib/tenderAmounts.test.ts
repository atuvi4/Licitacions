import { describe, expect, it } from "vitest";

import { tenderAmounts } from "@/lib/tenders";

describe("tenderAmounts (P2-06)", () => {
  it("cap import: cap xifra, i res calculat", () => {
    expect(tenderAmounts({ estimated_value: null, base_budget_without_tax: null, base_budget: null })).toEqual({ primary: null, budget: null });
  });

  it("l'ordre de la principal és el del filtre i l'ordenació: VEC → PBL sense IVA → PBL amb IVA", () => {
    expect(tenderAmounts({ estimated_value: "10", base_budget_without_tax: "8", base_budget: "9.68" }).primary).toEqual({ value: "10", basis: "vec" });
    expect(tenderAmounts({ estimated_value: null, base_budget_without_tax: "8", base_budget: "9.68" }).primary).toEqual({ value: "8", basis: "pbl_net" });
    expect(tenderAmounts({ estimated_value: null, base_budget_without_tax: null, base_budget: "9.68" }).primary).toEqual({ value: "9.68", basis: "pbl_gross" });
  });

  it("amb VEC i PBL sense IVA diferents, el PBL també surt (L'Escala)", () => {
    expect(tenderAmounts({ estimated_value: "245079.30", base_budget_without_tax: "204232.75", base_budget: null })).toEqual({
      primary: { value: "245079.30", basis: "vec" },
      budget: { value: "204232.75", basis: "pbl_net" },
    });
  });

  it("si són el mateix import (encara que escrit diferent), no es repeteix", () => {
    expect(tenderAmounts({ estimated_value: "1000.00", base_budget_without_tax: "1000", base_budget: null }).budget).toBeNull();
  });

  it("sense PBL sense IVA, el PBL amb IVA publicat surt amb la seva base; mai no se'n calcula l'IVA", () => {
    expect(tenderAmounts({ estimated_value: "10", base_budget_without_tax: null, base_budget: "12.10" }).budget).toEqual({ value: "12.10", basis: "pbl_gross" });
    expect(tenderAmounts({ estimated_value: "10", base_budget_without_tax: null, base_budget: null }).budget).toBeNull();
  });

  it("un camp absent (llistes sense `base_budget_without_tax`) es tracta com a no publicat", () => {
    expect(tenderAmounts({ estimated_value: null, base_budget: null })).toEqual({ primary: null, budget: null });
  });
});
