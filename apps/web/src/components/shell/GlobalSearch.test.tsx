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

describe("GlobalSearch · amplada útil (auditoria UI P1-03)", () => {
  // jsdom no calcula el layout: la mesura real és la verificació amb Chrome headless a
  // 375/768/1024/1035/1040/1050/1100/1280/1440 px. Aquí es guarden les classes que la fan possible.
  it("el camp no es pot encongir per sota d'una amplada útil", () => {
    render(<GlobalSearch catalogueTotal={17336} />);
    expect(screen.getByRole("searchbox").className).toMatch(/min-w-\[8rem\]/);
    expect(screen.getByRole("searchbox").className).not.toMatch(/(^|\s)min-w-0(\s|$)/);
  });

  it("el comptador i la drecera només apareixen quan hi ha espai (xl), mai a costa del camp", () => {
    const { container } = render(<GlobalSearch catalogueTotal={17336} />);
    const counter = [...container.querySelectorAll("span")].find((el) => el.textContent === "17336 licitacions al catàleg")!;
    expect(counter.className).toMatch(/hidden/);
    expect(counter.className).toMatch(/xl:inline/);
    expect(counter.className).not.toMatch(/(^|\s)md:inline/);
    const kbd = container.querySelector("kbd")!;
    expect(kbd.className).toMatch(/xl:inline/);
    expect(kbd.className).not.toMatch(/(^|\s)lg:inline/);
  });

  it("Ctrl+K posa el focus al camp de cerca", () => {
    render(<GlobalSearch catalogueTotal={17336} />);
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(screen.getByRole("searchbox"));
  });
});
