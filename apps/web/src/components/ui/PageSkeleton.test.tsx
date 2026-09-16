import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageSkeleton } from "./PageSkeleton";

describe("PageSkeleton", () => {
  it("pinta el títol, el context i les columnes reals abans que arribin les dades", () => {
    render(
      <PageSkeleton
        variant="table"
        title="Oportunitats"
        context="carregant les licitacions…"
        columns={["Encaix", "Licitació", "Termini"]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Oportunitats" })).toBeTruthy();
    expect(screen.getByText("carregant les licitacions…")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Termini" })).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("cada variant reserva una alçada: mai un spinner a pantalla sencera", () => {
    for (const variant of ["list", "detail", "decision"] as const) {
      const { container, unmount } = render(<PageSkeleton variant={variant} />);
      expect(container.querySelectorAll(".animate-skeleton").length).toBeGreaterThan(0);
      expect(container.querySelector(".animate-spin")).toBeNull();
      unmount();
    }
  });
});
