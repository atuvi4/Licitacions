import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card, CardHeader, PageHeader } from "./Card";

describe("Card", () => {
  it("amb frame porta les marques de registre; sense, cap", () => {
    const { container, rerender } = render(<Card frame>Decisió</Card>);
    expect(container.querySelectorAll("[data-mark]")).toHaveLength(4);
    rerender(<Card>Panell</Card>);
    expect(container.querySelectorAll("[data-mark]")).toHaveLength(0);
  });

  it("CardHeader i PageHeader titulen amb encapçalaments", () => {
    render(
      <Card>
        <CardHeader title="Decisions pendents" description="Ordenades per termini" action={<span>3</span>} />
      </Card>,
    );
    expect(screen.getByRole("heading", { name: "Decisions pendents" })).toBeTruthy();
    render(<PageHeader title="Avui" description="Dimecres 16 de setembre" />);
    expect(screen.getByRole("heading", { level: 1, name: "Avui" })).toBeTruthy();
  });
});
