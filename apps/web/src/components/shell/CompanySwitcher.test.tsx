import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Membership } from "@/lib/types";

import { CompanySwitcher } from "./CompanySwitcher";

const demo: Membership = {
  id: "m1",
  company_id: "c1",
  role: "owner",
  is_default: true,
  company: { id: "c1", legal_name: "Empresa Demo Telecom SL", trade_name: "Demo Telecom" },
};
const other: Membership = {
  id: "m2",
  company_id: "c2",
  role: "viewer",
  is_default: false,
  company: { id: "c2", legal_name: "Altra Empresa Demo SL", trade_name: null },
};

describe("CompanySwitcher", () => {
  it("amb una sola empresa és text pla dins la mateixa caixa, sense <select>", () => {
    render(<CompanySwitcher membership={demo} companies={[demo]} selectCompany={() => undefined} />);
    expect(screen.getByText("Empresa Demo Telecom SL")).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("amb més d'una empresa és un selector que crida selectCompany amb l'id", () => {
    const selectCompany = vi.fn();
    render(<CompanySwitcher membership={demo} companies={[demo, other]} selectCompany={selectCompany} />);
    const select = screen.getByRole("combobox", { name: "Empresa activa" }) as HTMLSelectElement;
    expect(select.value).toBe("c1");
    fireEvent.change(select, { target: { value: "c2" } });
    expect(selectCompany).toHaveBeenCalledWith("c2");
  });
});
