import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Button, LinkButton } from "./Button";

describe("Button", () => {
  it("el primari porta les quatre marques de registre", () => {
    const { container } = render(<Button variant="primary">Acció principal</Button>);
    expect(container.querySelectorAll("[data-mark]")).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Acció principal" })).toBeTruthy();
  });

  it("el secundari no porta marques", () => {
    const { container } = render(<Button variant="secondary">Acció secundària</Button>);
    expect(container.querySelectorAll("[data-mark]")).toHaveLength(0);
  });

  it("desactivat baixa al 45 % i no s'amaga", () => {
    render(<Button disabled>Desa</Button>);
    const button = screen.getByRole("button", { name: "Desa" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.className).toContain("disabled:opacity-45");
  });

  it("carregant desactiva el botó i mostra l'indicador", () => {
    render(<Button loading>Analitzant</Button>);
    const button = screen.getByRole("button") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByRole("status")).toBeTruthy();
  });

  it("LinkButton renderitza un enllaç amb aspecte de botó", () => {
    render(<LinkButton href="/licitacions">Obre</LinkButton>);
    const link = screen.getByRole("link", { name: "Obre" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/licitacions");
  });
});
