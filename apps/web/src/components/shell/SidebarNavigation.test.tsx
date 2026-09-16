import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SidebarNavigation } from "./SidebarNavigation";

describe("SidebarNavigation", () => {
  it("mostra els quatre destins amb els recomptes que se saben i cap zero per error", () => {
    render(
      <SidebarNavigation
        pathname="/licitacions"
        onNavigate={() => undefined}
        counts={{ oportunitats: 148, candidatures: 3, perfil: "96 %" }}
      />,
    );
    expect(screen.getByRole("link", { name: /Avui/ }).textContent).toBe("Avui");
    expect(screen.getByRole("link", { name: /Oportunitats/ }).textContent).toContain("148");
    expect(screen.getByRole("link", { name: /Candidatures/ }).textContent).toContain("3");
    expect(screen.getByRole("link", { name: /El nostre perfil/ }).textContent).toContain("96 %");
    expect(screen.getByRole("link", { name: /Oportunitats/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "Biblioteca de documents" })).toBeNull();
  });

  it("dins d'Empresa despleguen els tres subdestins", () => {
    render(<SidebarNavigation pathname="/experiencia" onNavigate={() => undefined} counts={{}} />);
    expect(screen.getByRole("link", { name: /El nostre perfil/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Experiència" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Biblioteca de documents" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Totes les àrees" })).toBeTruthy();
  });
});
