import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PackageNotice } from "@/app/(app)/licitacions/[tenderId]/PlecsOverview";
import { BLOCKING_REASON_TEXT, blockingReasonText, type AnalysisQuote, type QuotePrerequisites } from "@/lib/jobs";
import { PACKAGE_COVERAGE_TEXT, packageCoverageState } from "@/lib/tenders";

import { AnalysisQuotePanel } from "./AnalysisQuotePanel";

function quote(overrides: Partial<AnalysisQuote> = {}, prerequisites: Partial<QuotePrerequisites> = {}): AnalysisQuote {
  return {
    id: "q1",
    tender_id: "t1",
    status: "open",
    planning_version: "analysis-planning-v2",
    profile: "v1",
    provider: "anthropic",
    model: "claude-sonnet-x",
    force: false,
    input_fingerprint: "f",
    documents: [{ tender_document_id: "d1", name: "PCAP.pdf", document_type: "administrative_terms", pages: 30, chars: 90000 }],
    skipped_documents: [],
    pages_total: 30,
    pages_in_plan: 30,
    pages_truncated: false,
    chunks: 7,
    chunks_to_send: 7,
    reusable_run_id: null,
    estimated_input_tokens: 70000,
    estimated_output_tokens: 39000,
    max_chunks: 40,
    allowed: true,
    blocking_reason: null,
    prerequisites: {
      version: "quote-prerequisites-v2",
      provider_basis: "real",
      failed: [],
      advisory: [],
      warnings: [],
      ...prerequisites,
    },
    provider_basis: prerequisites.provider_basis ?? "real",
    warnings: prerequisites.warnings ?? [],
    estimated_cost_usd: 0.8,
    accept_incomplete_package: false,
    expires_at: "2026-10-08T10:00:00Z",
    ...overrides,
  };
}

function renderPanel(value: AnalysisQuote, extra: Partial<React.ComponentProps<typeof AnalysisQuotePanel>> = {}) {
  return render(
    <AnalysisQuotePanel
      quote={value}
      notice={null}
      canConfirm
      rerun={false}
      confirming={false}
      onConfirm={() => undefined}
      onCancel={() => undefined}
      onAccept={() => undefined}
      {...extra}
    />,
  );
}

describe("AnalysisQuotePanel (3B-2 §6.1, §7.1.5)", () => {
  it("diu sobre què es faria i el cost estimat quan hi ha preu", () => {
    const { container } = renderPanel(quote());
    expect(screen.getByText(/Lectura real del plec/)).toBeTruthy();
    expect(container.textContent).toMatch(/Cost estimat[^]*0,80 \$/);
    expect(container.textContent).not.toMatch(/provider_basis|real\b.*simulation/);
  });

  it("sense preu no s'inventa cap cost", () => {
    const { container } = renderPanel(quote({ estimated_cost_usd: null }));
    expect(container.textContent).toContain("sense preu conegut");
  });

  it("una simulació s'explica: mai serà vigent", () => {
    renderPanel(
      quote(
        { allowed: false, blocking_reason: "provider_not_real", estimated_cost_usd: null },
        { provider_basis: "simulation", failed: ["provider_not_real"] },
      ),
    );
    expect(screen.getByText(/Simulació: no llegeix el plec i el resultat no serà mai vigent/)).toBeTruthy();
  });

  it("amb `allowed=false` el botó és desactivat i el motiu es diu en català, sense codis", () => {
    const onConfirm = vi.fn();
    const { container } = renderPanel(
      quote({ allowed: false, blocking_reason: "budget_exhausted" }, { failed: ["budget_exhausted", "quality_unknown"] }),
      { onConfirm },
    );
    const button = screen.getByRole("button", { name: "Inicia l'anàlisi" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(`No es pot iniciar: ${BLOCKING_REASON_TEXT.budget_exhausted}${BLOCKING_REASON_TEXT.quality_unknown}`);
    expect(container.textContent).toContain(BLOCKING_REASON_TEXT.quality_unknown);
    expect(container.textContent).not.toMatch(/budget_exhausted|quality_unknown/);
  });

  it("tots els codis de `blocking_reason` tenen text humà", () => {
    for (const code of [
      "provider_not_real",
      "model_not_pinned",
      "price_unknown",
      "budget_exhausted",
      "budget_exceeded",
      "package_incomplete",
      "quality_unknown",
      "quality_warning",
      "selection_not_canonical",
      "analysis_too_large",
    ]) {
      expect(BLOCKING_REASON_TEXT[code], code).toBeTruthy();
      expect(BLOCKING_REASON_TEXT[code]).not.toContain("_");
    }
  });

  it("paquet incomplet: casella explícita per acceptar-lo, que torna a demanar el volum amb l'acceptació", () => {
    const onAccept = vi.fn();
    renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "incomplete", issues: [{ code: "package_gap" }], accepted: false } },
      ),
      { onAccept },
    );
    const box = screen.getByRole("checkbox", { name: /Accepto analitzar un paquet incomplet/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    expect(onAccept).toHaveBeenCalledWith({ accept_incomplete_package: true });
  });

  it("la casella del paquet incomplet diu què falta: cada forat amb el que aportaria o «impacte desconegut» (M4)", () => {
    const { container } = renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "incomplete", issues: [{ code: "package_gap", gap_code: "required_role_missing" }], accepted: false } },
      ),
      {
        pkg: {
          status: "incomplete",
          gaps: [
            { code: "required_role_missing", key: "ppt", would_provide: ["requirements", "scope_items"] },
            { code: "no_text", key: "d9", would_provide: [] },
          ],
          section_status: {},
        },
        documents: [{ id: "d9", name: "Plec escanejat.pdf" }],
      },
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Falta el plec de prescripcions tècniques (PPT)");
    expect(text).toContain("aportaria: requisits, abast");
    expect(text).toContain("Plec escanejat.pdf: present, sense text llegible per a l'anàlisi");
    expect(text).toContain("impacte desconegut");
    expect(text).not.toMatch(/required_role_missing|no_text|package_gap|scope_items/);
  });

  it("sense el paquet de la fitxa, la casella encara diu què falta a partir dels motius de la quota (M4)", () => {
    const { container } = renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "incomplete", issues: [{ code: "package_gap", gap_code: "referenced_document_missing" }, { code: "document_not_processed", tender_document_id: "d1" }], accepted: false } },
      ),
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Falta un document que cita el plec");
    expect(text).toContain("impacte desconegut");
    expect(text).toContain("PCAP.pdf: sense processar");
    expect(text).not.toMatch(/package_gap|referenced_document_missing|document_not_processed/);
  });

  it("F10: paquet sense verificar (classificació no vigent) → mai «hi falten documents»; casella de paquet sense verificar", () => {
    const pkg = { status: "unknown" as const, gaps: [], section_status: {} };
    const { container } = renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "unknown", issues: [{ code: "classification_not_current" }], accepted: false } },
      ),
      { pkg },
    );
    const text = container.textContent ?? "";
    expect(text).toContain(PACKAGE_COVERAGE_TEXT.unknown);
    expect(text).toContain("Encara no s'ha verificat si el paquet documental és complet");
    expect(text).not.toMatch(/falten documents|hi falten|Falta part|classificació no vigent/i);
    expect(screen.getByRole("checkbox", { name: /Accepto analitzar un paquet sense verificar/ })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /paquet incomplet/ })).toBeNull();
    // La mateixa semàntica i el mateix text que la pestanya Plecs.
    const { container: plecs } = render(<PackageNotice pkg={pkg} documents={[]} />);
    expect(plecs.textContent).toContain(PACKAGE_COVERAGE_TEXT.unknown);
  });

  it("F10: un estat `incomplete` sense cap motiu explícit (només classificació no vigent) també és «sense verificar»", () => {
    const { container } = renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "incomplete", issues: [{ code: "classification_not_current" }], accepted: false } },
      ),
    );
    expect(container.textContent).toContain(PACKAGE_COVERAGE_TEXT.unknown);
    expect(container.textContent).not.toMatch(/falten documents|hi falten/i);
  });

  it("F10: amb forats explícits → «Falten documents» amb la llista, igual que a Plecs", () => {
    const pkg = {
      status: "incomplete" as const,
      gaps: [{ code: "required_role_missing" as const, key: "ppt", would_provide: ["requirements"] }],
      section_status: {},
    };
    const { container } = renderPanel(
      quote(
        { allowed: false, blocking_reason: "package_incomplete" },
        { failed: ["package_incomplete"], package: { status: "incomplete", issues: [{ code: "classification_not_current" }, { code: "package_gap", gap_code: "required_role_missing" }], accepted: false } },
      ),
      { pkg },
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Falten documents");
    expect(text).toContain("Falta el plec de prescripcions tècniques (PPT)");
    expect(screen.getByRole("checkbox", { name: /Accepto analitzar un paquet incomplet/ })).toBeTruthy();
    const { container: plecs } = render(<PackageNotice pkg={pkg} documents={[]} />);
    expect(plecs.textContent).toContain("Falten documents");
  });

  it("F10: la semàntica és una sola funció (quota i Plecs)", () => {
    expect(packageCoverageState({ status: "unknown", gaps: [], section_status: {} })).toBe("unknown");
    expect(packageCoverageState({ status: "incomplete", gaps: [], section_status: {} })).toBe("unknown");
    expect(packageCoverageState({ status: "incomplete", gaps: [{ code: "no_text", key: "d1", would_provide: [] }], section_status: {} })).toBe("incomplete");
    expect(packageCoverageState({ status: "complete", gaps: [], section_status: {} })).toBe("complete");
    expect(packageCoverageState(null)).toBe("unknown");
    expect(packageCoverageState(null, ["classification_not_current"])).toBe("unknown");
    expect(packageCoverageState(null, ["classification_not_current", "package_gap"])).toBe("incomplete");
    expect(packageCoverageState(null, ["duplicate_content"])).toBe("incomplete");
    expect(blockingReasonText("package_incomplete")).not.toMatch(/falten|hi falten/i);
    expect(blockingReasonText("package_incomplete", "incomplete")).toMatch(/^Falten documents/);
  });

  it("només avisos de processament: casella pròpia que enumera els documents, mai «paquet incomplet»", () => {
    const onAccept = vi.fn();
    renderPanel(
      quote(
        {
          allowed: false,
          blocking_reason: "processing_incomplete",
          documents: [
            { tender_document_id: "d1", name: "PCAP.pdf", document_type: "administrative_terms", pages: 30, chars: 90000 },
          ],
          skipped_documents: [
            { tender_document_id: "d9", name: "06_Plantilla.xlsx", document_type: "additional", reason: "document_not_analyzable" },
          ],
        },
        {
          failed: ["processing_incomplete"],
          package: { status: "complete", issues: [], accepted: false },
          processing: {
            status: "partial",
            issues: [
              { code: "default_document_skipped", tender_document_id: "d9", reason: "document_not_analyzable" },
              { code: "package_gap", gap_code: "format_not_analyzable", gap_id: "g1", tender_document_id: "d9" },
            ],
            acknowledged: false,
          },
        },
      ),
      { onAccept, pkg: { status: "complete", gaps: [], processing: [], section_status: {} } },
    );
    expect(document.body.textContent).toContain(BLOCKING_REASON_TEXT.processing_incomplete);
    const box = screen.getByLabelText(/Accepto que hi ha documents presents que Tuvi encara no pot processar completament/);
    const list = screen.getByRole("list", { name: "Documents presents que no es poden processar completament" });
    expect(list.textContent).toContain("06_Plantilla.xlsx: en un format o estat que Tuvi encara no pot llegir");
    expect(list.querySelectorAll("li")).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/paquet incomplet|Falten documents/i);
    fireEvent.click(box);
    expect(onAccept).toHaveBeenCalledWith({ acknowledge_processing: true });
  });

  it("text amb avisos de qualitat: casella explícita per reconèixer-ho", () => {
    const onAccept = vi.fn();
    renderPanel(quote({ allowed: false, blocking_reason: "quality_warning" }, { failed: ["quality_warning"] }), { onAccept });
    fireEvent.click(screen.getByRole("checkbox", { name: /Accepto que el text té avisos de qualitat/ }));
    expect(onAccept).toHaveBeenCalledWith({ acknowledge_quality: true });
  });

  it("una acceptació ja feta es veu marcada i el botó es pot fer servir", () => {
    renderPanel(quote({ accept_incomplete_package: true }, { accept_incomplete_package: true, package: { status: "incomplete", issues: [{ code: "package_gap" }], accepted: true } }));
    expect((screen.getByRole("checkbox", { name: /Accepto analitzar un paquet incomplet/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("button", { name: "Inicia l'anàlisi" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("l'última anàlisi d'aquests plecs va acabar sense resultats: es diu abans de tornar-la a demanar", () => {
    renderPanel(quote({}, { warnings: ["previous_run_completed_empty"] }));
    expect(screen.getByText(/anàlisi acabada sense resultats: cal revisar/)).toBeTruthy();
  });
});
