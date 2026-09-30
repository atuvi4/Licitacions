import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/documents" }));

import { ProfileSubnav } from "./ProfileSubnav";

describe("ProfileSubnav", () => {
  it("llista les quatre seccions i marca la secció actual", () => {
    render(<ProfileSubnav />);
    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["Totes les àrees", "Coneixement", "Biblioteca de documents", "Experiència"]);
    expect(screen.getByRole("link", { name: "Biblioteca de documents" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Totes les àrees" }).getAttribute("aria-current")).toBeNull();
  });
});
