import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EvidenceRef } from "./EvidenceRef";

const href = "/licitacions/t1/documents/d1?page=3&extraction=e1";

describe("EvidenceRef", () => {
  it("compon document, pàgina i revisió, i enllaça a la revisió citada", () => {
    render(
      <EvidenceRef
        documentName="Plec administratiu"
        pageNumber={3}
        extractionRevision={1}
        href={href}
        quote="…un límite total de 600.000 euros…"
      />,
    );
    expect(screen.getByText("Plec administratiu · p. 3 · revisió 1")).toBeTruthy();
    const link = screen.getByRole("link", { name: /Obre la pàgina 3 del plec/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(href);
    expect(screen.getByText(/600.000 euros/)).toBeTruthy();
  });

  it("en mode compacte només mostra la línia", () => {
    render(
      <EvidenceRef documentName="Plec tècnic" pageNumber={12} extractionRevision={2} href={href} quote="cita" compact />,
    );
    expect(screen.getByText("Plec tècnic · p. 12 · revisió 2")).toBeTruthy();
    expect(screen.queryByText("cita")).toBeNull();
  });

  it("sense cita ho diu, i el requisit no s'amaga", () => {
    render(<EvidenceRef documentName={null} pageNumber={null} href={null} />);
    expect(screen.getByText("Sense cita al plec")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
