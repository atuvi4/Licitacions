import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { GlobalSearch } from "./GlobalSearch";

describe("GlobalSearch", () => {
  beforeEach(() => {
    push.mockClear();
  });

  it("el placeholder cerca per títol, expedient, organisme o descripció; el comptador és el del catàleg", () => {
    const { container, unmount } = render(<GlobalSearch catalogueTotal={148} />);
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Cerca per títol, expedient, organisme o descripció");
    expect(container.textContent).toContain("148 licitacions al catàleg");
    expect(container.textContent).not.toMatch(/oportunitats obertes|candidat/);
    unmount();
    const single = render(<GlobalSearch catalogueTotal={1} />);
    expect(single.container.textContent).toContain("1 licitació al catàleg");
    single.unmount();
    const unknown = render(<GlobalSearch catalogueTotal={null} />);
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Cerca per títol, expedient, organisme o descripció");
    expect(unknown.container.textContent).not.toContain("al catàleg");
  });

  it("en enviar navega a Oportunitats amb ?q= i ignora una cerca buida", () => {
    render(<GlobalSearch catalogueTotal={148} />);
    const input = screen.getByRole("searchbox");
    fireEvent.change(input, { target: { value: "  fibra fosca " } });
    fireEvent.submit(input.closest("form")!);
    expect(push).toHaveBeenCalledWith("/licitacions?q=fibra+fosca");
    push.mockClear();
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form")!);
    expect(push).not.toHaveBeenCalled();
  });
});
