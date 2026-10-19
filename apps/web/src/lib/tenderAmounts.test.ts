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

  it("zeros (19-10-2026): un 0,00 € publicat no és cap xifra, en el mateix ordre que el filtre", () => {
    // VEC 0 i PBL > 0 (46 al pilot): la principal és el PBL sense IVA.
    expect(tenderAmounts({ estimated_value: "0.00", base_budget_without_tax: "30000.00", base_budget: null })).toEqual({
      primary: { value: "30000.00", basis: "pbl_net" },
      budget: null,
    });
    // VEC > 0 i PBL 0 (228): el VEC, sense la línia «PBL sense IVA: 0,00 €».
    expect(tenderAmounts({ estimated_value: "50000.00", base_budget_without_tax: "0.00", base_budget: null })).toEqual({
      primary: { value: "50000.00", basis: "vec" },
      budget: null,
    });
    // Tots dos a 0 (38) o nul i 0 (7): el mateix estat que sense import.
    expect(tenderAmounts({ estimated_value: "0.00", base_budget_without_tax: "0.00", base_budget: null })).toEqual({ primary: null, budget: null });
    expect(tenderAmounts({ estimated_value: null, base_budget_without_tax: "0.00", base_budget: null })).toEqual({ primary: null, budget: null });
    // Un PBL amb IVA a 0 tampoc no fa de PBL; ni un negatiu.
    expect(tenderAmounts({ estimated_value: "10", base_budget_without_tax: "0", base_budget: "0.00" }).budget).toBeNull();
    expect(tenderAmounts({ estimated_value: "-1", base_budget_without_tax: null, base_budget: "12.10" }).primary).toEqual({ value: "12.10", basis: "pbl_gross" });
  });
});
