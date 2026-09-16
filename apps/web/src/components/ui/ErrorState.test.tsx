import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("diu què ha passat, ofereix l'acció i amaga el codi al detall tècnic", () => {
    const { container } = render(
      <ErrorState
        title="No hem pogut carregar les oportunitats"
        description="La connexió amb el servei ha fallat. No s'ha perdut res."
        action={<button type="button">Torna-ho a provar</button>}
        technical={{ code: "internal_error", requestId: "7f3ac21b", method: "GET /tenders", at: "16 set. 11:04" }}
      />,
    );
    const heading = screen.getByRole("heading", { name: "No hem pogut carregar les oportunitats" });
    expect(heading.textContent).not.toContain("internal_error");
    expect(screen.getByRole("button", { name: "Torna-ho a provar" })).toBeTruthy();
    const details = container.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain("Detall tècnic");
    expect(details?.textContent).toContain("internal_error");
    expect(details?.textContent).toContain("7f3ac21b");
  });

  it("sense detall tècnic no renderitza cap desplegable", () => {
    const { container } = render(
      <ErrorState
        title="Alguna cosa ha fallat"
        description="Pots continuar per una altra banda."
        action={<button type="button">Torna</button>}
      />,
    );
    expect(container.querySelector("details")).toBeNull();
  });
});
