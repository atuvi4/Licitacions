import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/licitacions",
  useSearchParams: () => new URLSearchParams("q=fibra"),
}));

import { CatalogueTabs } from "./CatalogueTabs";

describe("CatalogueTabs", () => {
  it("dues pestanyes, «Totes» i «Radar · prioritzades» (D2), amb la vigent marcada", () => {
    render(<CatalogueTabs value="totes" />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Totes", "Radar · prioritzades"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
  });

  it("canviar de pestanya escriu ?vista= a la URL i conserva la resta de paràmetres", () => {
    render(<CatalogueTabs value="totes" />);
    fireEvent.click(screen.getByRole("tab", { name: "Radar · prioritzades" }));
    expect(push).toHaveBeenCalledWith("/licitacions?q=fibra&vista=revisar", { scroll: false });
  });
});
