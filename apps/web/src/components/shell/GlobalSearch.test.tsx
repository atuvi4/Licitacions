import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { GlobalSearch } from "./GlobalSearch";

describe("GlobalSearch", () => {
  beforeEach(() => {
    push.mockClear();
  });

  it("diu entre quantes oportunitats cerca, o el text genèric si no ho sap", () => {
    const { unmount } = render(<GlobalSearch candidates={148} />);
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Cerca entre les 148 oportunitats obertes");
    unmount();
    const single = render(<GlobalSearch candidates={1} />);
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Cerca entre 1 oportunitat oberta");
    single.unmount();
    render(<GlobalSearch candidates={null} />);
    expect(screen.getByRole("searchbox").getAttribute("placeholder")).toBe("Cerca licitació, expedient, organisme");
  });

  it("en enviar navega a Oportunitats amb ?q= i ignora una cerca buida", () => {
    render(<GlobalSearch candidates={148} />);
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
