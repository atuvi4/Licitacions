import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatePills } from "./StatePills";

describe("StatePills", () => {
  it("declarat sense verificar: només «Declarat», cap «Pendent de verificar» ni «Falta evidència» (B2)", () => {
    render(<StatePills value={{ state: "declared", flags: ["to_verify"] }} />);
    expect(screen.getByText("Declarat")).toBeTruthy();
    expect(screen.queryByText("Pendent de verificar")).toBeNull();
    expect(screen.queryByText("Falta evidència")).toBeNull();
  });

  it("verificat es manté, i «Abast desconegut» també", () => {
    render(<StatePills value={{ state: "verified", flags: ["scope_unknown"] }} />);
    expect(screen.getByText("Verificat")).toBeTruthy();
    expect(screen.getByText("Abast desconegut")).toBeTruthy();
  });
});
