import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PACKAGE_COVERAGE_TEXT, type TenderDocumentMeta, type TenderPackage } from "@/lib/tenders";

import { DocumentBadges, PackageNotice, PlecsReadingLine, RemovedDocuments } from "./PlecsOverview";

function doc(overrides: Partial<TenderDocumentMeta>): TenderDocumentMeta {
  return {
    id: "d1",
    name: "PCAP.pdf",
    document_type: "administrative_terms",
    source_document_type_code: null,
    url: "https://contrataciondelestado.es/d1",
    mime_type: "application/pdf",
    published_at: null,
    removed_at: null,
    latest_file: null,
    origin: "feed",
    contribution_status: "not_applicable",
    duplicate_of_document_id: null,
    analyzable: true,
    ...overrides,
  };
}

describe("Plecs: estat honest del paquet", () => {
  it("badge «pendent de verificació» i origen d'una aportació de l'operador", () => {
    const pending = doc({ id: "p", origin: "operator_url", contribution_status: "pending_verification", analyzable: false });
    render(<DocumentBadges document={pending} documents={[pending]} />);
    expect(screen.getByText("pendent de verificació")).toBeTruthy();
    expect(screen.getByText("aportat per l'operador")).toBeTruthy();
    expect(screen.getByText(/no entra a l'anàlisi fins que l'operador el verifiqui/)).toBeTruthy();
  });

  it("els àlies es pleguen sota el canònic amb «àlies de …»", () => {
    const canonical = doc({ id: "c", name: "PCAP.pdf" });
    const alias = doc({ id: "a", name: "PCAP (còpia).pdf", duplicate_of_document_id: "c", analyzable: false });
    render(<DocumentBadges document={canonical} documents={[canonical, alias]} />);
    expect(screen.getByText(/PCAP \(còpia\)\.pdf/)).toBeTruthy();
    expect(screen.getByText("àlies de PCAP.pdf")).toBeTruthy();
  });

  it("un retirat es veu amb la data, a part, i s'atribueix a qui l'ha retirat (M3)", () => {
    const { container } = render(
      <RemovedDocuments
        documents={[
          doc({ id: "r", name: "PPT antic.pdf", removed_at: "2026-09-30T10:00:00Z" }),
          doc({ id: "o", name: "Annex operador.pdf", origin: "operator_url", removed_at: "2026-10-01T10:00:00Z" }),
        ]}
      />,
    );
    expect(screen.getByText("PPT antic.pdf")).toBeTruthy();
    expect(screen.getByText(/Retirat de la font \(des del /)).toBeTruthy();
    expect(screen.getByText(/Retirat per l'operador \(des del /)).toBeTruthy();
    // El títol de la secció no atribueix res a la font: hi ha retirats de l'operador.
    expect(container.textContent).not.toContain("Retirats de la font");
    expect(container.textContent).toContain("Documents retirats");
  });

  it("la línia de cobertura del paquet hi és sempre, també quan és complet (A4)", () => {
    const { container } = render(<PackageNotice pkg={{ status: "complete", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).toContain("Paquet complet");
    expect(container.textContent).not.toMatch(/falten documents/);
  });

  it("«N de M documents analitzables llegits»", () => {
    render(<PlecsReadingLine documents={[doc({ id: "1" }), doc({ id: "2", analyzable: false })]} />);
    expect(screen.getByText("0 de 1 document analitzable llegit")).toBeTruthy();
  });

  it("avís de paquet incomplet amb què falta i el seu impacte (o «impacte desconegut»)", () => {
    const pkg: TenderPackage = {
      status: "incomplete",
      gaps: [
        { code: "required_role_missing", key: "ppt", would_provide: ["requirements", "scope_items"] },
        { code: "referenced_document_missing", key: "DOC-03", would_provide: [] },
      ],
      section_status: { scope_items: "unknown" },
    };
    const { container } = render(<PackageNotice pkg={pkg} documents={[]} />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/Paquet incomplet/i);
    expect(text).toContain("Falta el plec de prescripcions tècniques (PPT)");
    expect(text).toContain("aportaria: requisits, abast");
    expect(text).toContain("Falta un document que cita el plec (DOC-03)");
    expect(text).toContain("impacte desconegut");
    expect(text).not.toMatch(/required_role_missing|referenced_document_missing|scope_items/);
  });

  it("sense forats no hi ha cap avís de falta; amb el paquet sense verificar es diu que no se sap", () => {
    const { container, rerender } = render(<PackageNotice pkg={{ status: "complete", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).not.toMatch(/falten|Falta/);
    rerender(<PackageNotice pkg={{ status: "unknown", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).toContain("Encara no s'ha verificat si el paquet documental és complet.");
    rerender(<PackageNotice pkg={null} documents={[]} />);
    expect(container.textContent).toBe("");
  });

  it("F10: tres estats amb copy pròpia; un «incomplet» sense forats explícits no diu mai que falten documents", () => {
    const { container, rerender } = render(<PackageNotice pkg={{ status: "complete", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).toContain("verificat");
    rerender(<PackageNotice pkg={{ status: "unknown", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).toContain(PACKAGE_COVERAGE_TEXT.unknown);
    expect(container.textContent).not.toMatch(/falten|Falta/i);
    rerender(<PackageNotice pkg={{ status: "incomplete", gaps: [], section_status: {} }} documents={[]} />);
    expect(container.textContent).toContain(PACKAGE_COVERAGE_TEXT.unknown);
    expect(container.textContent).not.toMatch(/falten|Falta/i);
    rerender(<PackageNotice pkg={{ status: "incomplete", gaps: [{ code: "no_text", key: "d1", would_provide: [] }], section_status: {} }} documents={[{ ...doc({ id: "d1" }), name: "PPT.pdf" }]} />);
    expect(container.textContent).toContain("Falten documents");
    expect(container.textContent).toContain("PPT.pdf: sense text llegible");
  });
});
