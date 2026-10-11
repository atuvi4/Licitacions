import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();

vi.mock("next/navigation", () => ({ usePathname: () => "/avui" }));
vi.mock("@/lib/api", () => ({ api: { get: (path: string) => get(path) } }));
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    user: { id: "u", email: "u@example.com", full_name: "U" },
    membership: { role: "owner", company: { id: "c1", legal_name: "Empresa SL" } },
    companies: [],
    companyId: "c1",
    selectCompany: () => undefined,
    logout: () => undefined,
  }),
}));
vi.mock("./CompanySwitcher", () => ({ CompanySwitcher: () => null }));
vi.mock("./GlobalSearch", () => ({ GlobalSearch: () => null }));
vi.mock("./JobCenter", () => ({ JobCenter: () => null }));
vi.mock("./UserSummary", () => ({ UserSummary: () => null }));

import { AppFrame } from "./AppFrame";

describe("AppFrame: peu de la barra lateral", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockImplementation((path: string) => {
      if (path.endsWith("/summary") && !path.includes("matches")) {
        return Promise.resolve({
          counts: {},
          completeness: {
            sections: [{ key: "a", label: "A", weight: 1, ratio: 0.5, points: 1, status: "partial", detail: "", requires_evidence: false }],
          },
        });
      }
      return Promise.resolve([]);
    });
  });

  it("mostra el nom de l'empresa i cap indicador de completitud del perfil (B1)", async () => {
    render(<AppFrame>contingut</AppFrame>);
    await waitFor(() => expect(get).toHaveBeenCalledWith("/companies/c1/workspaces"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.getAllByText("Empresa SL").length).toBeGreaterThan(0);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/parcial|buida|no estan al dia|%/);
  });
});
