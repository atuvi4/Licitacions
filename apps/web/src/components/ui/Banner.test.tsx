import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Banner } from "./Banner";

describe("Banner", () => {
  it("mostra rètol, frase i acció amb el nivell com a atribut", () => {
    render(
      <Banner level="attention" kicker="Abans de decidir" action={<a href="#resol">Resol</a>}>
        2 requisits bloquegen.
      </Banner>,
    );
    const banner = screen.getByRole("status");
    expect(banner.getAttribute("data-level")).toBe("attention");
    expect(banner.textContent).toContain("Abans de decidir");
    expect(banner.textContent).toContain("2 requisits bloquegen.");
    expect(screen.getByRole("link", { name: "Resol" })).toBeTruthy();
  });

  it("només el nivell ready pot anar sense acció", () => {
    render(
      <Banner level="ready" kicker="Dossier complet">
        Tot a punt.
      </Banner>,
    );
    expect(screen.getByRole("status").getAttribute("data-level")).toBe("ready");
    // @ts-expect-error cap Banner sense acció fora de ready (handoff §2.4)
    const missing = <Banner level="blocking" kicker="Termini">Ha passat.</Banner>;
    expect(missing).toBeTruthy();
  });
});
