import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/licitacions/t1",
  useSearchParams: () => new URLSearchParams("tab=resum&q=fibra"),
}));

import { Tabs } from "./Tabs";

const items = [
  { key: "resum", label: "Resum" },
  { key: "requisits", label: "Requisits", count: 2, countLevel: "blocking" as const },
];

describe("Tabs", () => {
  beforeEach(() => {
    replace.mockClear();
  });

  it("marca la pestanya activa i mostra el recompte", () => {
    render(<Tabs items={items} value="resum" onChange={() => undefined} />);
    expect(screen.getByRole("tab", { name: "Resum" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: /Requisits/ }).textContent).toContain("2");
  });

  it("en canviar escriu ?tab= a la URL sense perdre els altres paràmetres i avisa onChange", () => {
    const onChange = vi.fn();
    render(<Tabs items={items} value="resum" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /Requisits/ }));
    expect(onChange).toHaveBeenCalledWith("requisits");
    expect(replace).toHaveBeenCalledWith("/licitacions/t1?tab=requisits&q=fibra", { scroll: false });
  });
});
