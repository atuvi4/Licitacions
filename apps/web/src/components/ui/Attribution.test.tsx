import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Attribution } from "./Attribution";

describe("Attribution", () => {
  it("la IA diu la confiança en text, no en color", () => {
    const { container } = render(<Attribution kind="ai" confidence="high" />);
    expect(container.querySelector("[data-kind='ai']")).not.toBeNull();
    expect(screen.getByText(/confiança alta/)).toBeTruthy();
  });

  it("el càlcul diu que ve del perfil", () => {
    render(<Attribution kind="rule" />);
    expect(screen.getByText(/calculat amb el perfil/i)).toBeTruthy();
  });

  it("la persona porta inicials, nom i data", () => {
    const { container } = render(
      <Attribution kind="human" person={{ name: "Anna Puig" }} at="2026-09-12T10:00:00Z" />,
    );
    const mark = container.querySelector("[data-kind='human']");
    expect(mark?.textContent).toBe("AP");
    expect(screen.getByText(/Anna Puig · .*2026/)).toBeTruthy();
  });

  it("una etiqueta explícita substitueix el text per defecte", () => {
    render(<Attribution kind="rule" label="Calculat amb el perfil · regles v3" />);
    expect(screen.getByText("Calculat amb el perfil · regles v3")).toBeTruthy();
  });
});
