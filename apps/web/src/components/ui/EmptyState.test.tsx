import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("mostra títol, explicació i l'acció", () => {
    render(
      <EmptyState
        title="Encara no sabem què busqueu"
        description="L'encaix es calcula amb els codis CPV."
        action={<button type="button">Completa el perfil</button>}
        secondary={<a href="#obertes">Mira les 148 obertes</a>}
      />,
    );
    expect(screen.getByRole("heading", { name: "Encara no sabem què busqueu" })).toBeTruthy();
    expect(screen.getByText("L'encaix es calcula amb els codis CPV.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Completa el perfil" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Mira les 148 obertes" })).toBeTruthy();
  });

  it("no compila sense acció: cap buit sense sortida", () => {
    // @ts-expect-error l'acció és obligatòria al tipus (handoff §2.7)
    const element = <EmptyState title="Sense sortida" />;
    expect(element).toBeTruthy();
  });
});
