import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill, type Level } from "./StatusPill";

const LEVELS: Level[] = ["neutral", "running", "ready", "attention", "blocking", "action"];

describe("StatusPill", () => {
  it.each(LEVELS)("exposa el nivell %s com a atribut i mostra el text", (level) => {
    render(<StatusPill level={level}>Etiqueta</StatusPill>);
    const pill = screen.getByText("Etiqueta");
    expect(pill.getAttribute("data-level")).toBe(level);
  });

  it("amb outline només dibuixa la vora", () => {
    render(
      <StatusPill level="blocking" outline>
        Bloquegen 2
      </StatusPill>,
    );
    const pill = screen.getByText("Bloquegen 2");
    expect(pill.getAttribute("data-outline")).toBe("true");
    expect(pill.className).toContain("border");
  });

  it("fa servir el radi de pastilla del sistema i el text no es trenca", () => {
    render(<StatusPill level="ready">A punt</StatusPill>);
    const pill = screen.getByText("A punt");
    expect(pill.className).toContain("whitespace-nowrap");
    expect(pill.className).toContain("rounded-pill");
  });
});
