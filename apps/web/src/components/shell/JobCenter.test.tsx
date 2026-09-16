import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));
vi.mock("next/navigation", () => ({ usePathname: () => "/licitacions" }));

import { JobCenter } from "./JobCenter";

const limits = { max_chunks_per_run: 40, max_active_per_company: 2, active_now: 0, can_confirm: true, can_process_documents: true, confirm_roles: ["owner"] };

describe("JobCenter", () => {
  // Amb claus: un hook que retorna una funció (el mock mateix) la fa servir de neteja.
  beforeEach(() => {
    get.mockReset();
  });

  it("sense feina diu «Cap tasca en curs» en contorn neutre", async () => {
    get.mockResolvedValue(limits);
    render(<JobCenter companyId="c1" />);
    const pill = await screen.findByText("Cap tasca en curs");
    expect(pill.getAttribute("data-level")).toBe("neutral");
    expect(pill.getAttribute("data-outline")).toBe("true");
    expect(get).toHaveBeenCalledWith("/companies/c1/analysis-limits");
  });

  it("amb feina compta les tasques i enllaça a Candidatures", async () => {
    get.mockResolvedValue({ ...limits, active_now: 2 });
    render(<JobCenter companyId="c1" />);
    const pill = await screen.findByText("2 tasques en curs");
    expect(pill.getAttribute("data-level")).toBe("running");
    expect(screen.getByRole("link").getAttribute("href")).toBe("/candidatures");
    get.mockResolvedValue({ ...limits, active_now: 1 });
    render(<JobCenter companyId="c2" />);
    expect(await screen.findByText("1 tasca en curs")).toBeTruthy();
  });

  it("si la crida falla no mostra res, mai un zero per error", async () => {
    get.mockImplementation(() => Promise.reject(new Error("503")));
    const { container } = render(<JobCenter companyId="c1" />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    // El rebuig s'ha d'assentar dins d'un `act` (React 19): una macrotasca hi basta.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.textContent).toBe("");
  });
});
