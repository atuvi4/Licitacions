import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TenderHistoryRevision } from "@/lib/tender-changes";

import { TenderChangeHistory } from "./TenderChangeHistory";

const AT = "2026-10-06T10:00:00Z";

function revision(overrides: Partial<TenderHistoryRevision>): TenderHistoryRevision {
  return {
    id: "r",
    revision_number: 1,
    trigger: "ingestion",
    origin: "source",
    created_at: AT,
    change_summary: null,
    events: [],
    ...overrides,
  };
}

function event(change_code: string, extra: Record<string, unknown> = {}) {
  return {
    change_code,
    subject: null,
    old_value: null,
    new_value: null,
    impacts: ["informational"],
    is_material: true,
    detected_at: AT,
    ...extra,
  } as TenderHistoryRevision["events"][number];
}

function renderHistory(revisions: TenderHistoryRevision[], documents?: Array<{ url: string; name: string }>) {
  return render(
    <TenderChangeHistory revisions={revisions} documents={documents} loading={false} error={null} onReload={() => undefined} />,
  );
}

const PCAP_URL = "https://contrataciondelestado.es/wps/download/pcap.pdf";
const ALIAS_URL = "https://contrataciondelestado.es/wps/download/pcap-copia.pdf";
const ANNEX_URL = "https://contractaciopublica.cat/ecofin/annex3.pdf";
const DOCUMENTS = [
  { url: PCAP_URL, name: "PCAP.pdf" },
  { url: ALIAS_URL, name: "PCAP (còpia).pdf" },
  { url: ANNEX_URL, name: "Annex 3.pdf" },
];

/** Els codis nous de 3B-2 amb els valors que de debò emet `app/tenders/changes.py`. */
const REAL_EVENTS = [
  event("DOCUMENT_CONTRIBUTION_CHANGED", { subject: ANNEX_URL, old_value: null, new_value: "pending_verification", is_material: false }),
  event("DOCUMENT_CONTRIBUTION_CHANGED", { subject: ANNEX_URL, old_value: "pending_verification", new_value: "analyzable", is_material: false }),
  event("DOCUMENT_CONTRIBUTION_CHANGED", { subject: ANNEX_URL, old_value: "pending_verification", new_value: "rejected", is_material: false }),
  event("DOCUMENT_CONTRIBUTION_CHANGED", { subject: ANNEX_URL, old_value: "rejected", new_value: "removed", is_material: false }),
  event("DOCUMENT_ALIAS_CHANGED", { subject: ALIAS_URL, old_value: null, new_value: PCAP_URL, is_material: false }),
  event("DOCUMENT_ALIAS_CHANGED", { subject: ALIAS_URL, old_value: PCAP_URL, new_value: null, is_material: false }),
  event("DOCUMENT_ROLE_REVIEWED", { subject: PCAP_URL, old_value: null, new_value: "characteristics_table", is_material: false }),
  event("DOCUMENT_ROLE_REVIEWED", { subject: PCAP_URL, old_value: "pcap", new_value: "inventory_annex", is_material: false }),
  event("SOURCE_TYPE_CHANGED_AFTER_REVIEW", { subject: PCAP_URL, old_value: "administrative_terms", new_value: "technical_terms", is_material: false }),
  event("DOCUMENT_URL_CHANGED", { subject: ALIAS_URL, old_value: PCAP_URL, new_value: ALIAS_URL, is_material: false }),
  event("DOCUMENT_METADATA_CHANGED", {
    subject: PCAP_URL,
    old_value: { name: "PCAP.pdf", document_type: "administrative_terms", published_at: null },
    new_value: { name: "PCAP definitiu.pdf", document_type: "technical_terms", published_at: null },
    is_material: false,
  }),
];

describe("TenderChangeHistory", () => {
  it("no mostra camps interns, booleans ni URLs sota els canvis de presència i de documents", () => {
    const url = "https://contrataciondelestado.es/wps/download/abc";
    const events = [
      event("SOURCE_ENTRY_RESTORED", { subject: "source_removed", old_value: true, new_value: false }),
      event("DOCUMENT_REMOVED", { subject: url, old_value: "Plec.pdf" }),
    ];
    const { container } = renderHistory([revision({ id: "r1", revision_number: 2, events })]);
    const text = container.textContent ?? "";
    expect(text).toContain("Torna a estar disponible a la font");
    expect(text).toContain("Document retirat: Plec.pdf");
    expect(text).not.toMatch(/source_removed|true|false|https?:/);
  });

  it("separa el que publica la font del processament intern i mai diu «l'òrgan» a l'intern", () => {
    renderHistory([
      revision({
        id: "r3",
        revision_number: 3,
        trigger: "document_intake",
        origin: "internal",
        events: [event("DOCUMENT_ALIAS_CHANGED"), event("DOCUMENT_ROLE_REVIEWED")],
      }),
      revision({
        id: "r2",
        revision_number: 2,
        trigger: "ingestion",
        origin: "source",
        events: [event("DEADLINE_CHANGED", { old_value: "2026-10-20T12:00:00Z", new_value: "2026-10-27T12:00:00Z" })],
      }),
      revision({ id: "r1", revision_number: 1 }),
    ]);

    expect(screen.getByRole("heading", { name: "Com ha evolucionat l'expedient" })).toBeTruthy();
    const source = screen.getByRole("region", { name: "Publicat per la font" });
    const internal = screen.getByRole("region", { name: "Processament intern (Tuvi)" });
    expect(within(source).getByText(/Termini de presentació modificat/)).toBeTruthy();
    expect(within(source).queryByText(/agrupat/)).toBeNull();
    expect(within(internal).getByText(/Document agrupat amb un altre amb el mateix contingut/)).toBeTruthy();
    expect(within(internal).getByText(/Tipus de document revisat per l'operador/)).toBeTruthy();
    expect(within(internal).getByText(/En incorporar o revisar documents del paquet/)).toBeTruthy();
    expect(internal.textContent ?? "").not.toMatch(/òrgan/i);
    // Cap codi cru, de canvi ni de disparador.
    expect(document.body.textContent ?? "").not.toMatch(/DOCUMENT_|document_intake|_CHANGED/);
    expect(document.body.textContent ?? "").not.toContain("la font no n'ha modificat cap dada");
  });

  it("«la font no n'ha modificat cap dada» només quan no hi ha canvis publicats per la font", () => {
    renderHistory([
      revision({
        id: "r2",
        revision_number: 2,
        trigger: "document_processing",
        origin: "internal",
        events: [event("DOCUMENT_CONTENT_IDENTIFIED", { is_material: false })],
      }),
      revision({ id: "r1", revision_number: 1 }),
    ]);

    const source = screen.getByRole("region", { name: "Publicat per la font" });
    expect(within(source).getByText(/la font no n'ha modificat cap dada/)).toBeTruthy();
    const internal = screen.getByRole("region", { name: "Processament intern (Tuvi)" });
    expect(internal.textContent ?? "").not.toContain("la font no n'ha modificat cap dada");
  });

  it("els codis nous del paquet tenen text humà i un codi desconegut no surt cru", () => {
    renderHistory([
      revision({
        id: "r2",
        revision_number: 2,
        trigger: "document_intake",
        origin: "internal",
        events: [
          event("DOCUMENT_CONTRIBUTION_CHANGED"),
          event("SOURCE_TYPE_CHANGED_AFTER_REVIEW"),
          event("A_CODE_NOBODY_KNOWS"),
        ],
      }),
    ]);
    const text = document.body.textContent ?? "";
    expect(text).toContain("Aportació de l'operador");
    expect(text).toContain("La font ha canviat el tipus d'un document ja revisat");
    expect(text).toContain("Canvi no reconegut");
    expect(text).not.toContain("A_CODE_NOBODY_KNOWS");
  });

  it("A3: «Versió N» només compta les revisions de la font; les internes diuen «Processament intern» i la data, sense número", () => {
    renderHistory(
      [
        revision({
          id: "r4",
          revision_number: 4,
          trigger: "ingestion",
          origin: "source",
          events: [event("DEADLINE_CHANGED", { old_value: "2026-10-20T12:00:00Z", new_value: "2026-10-27T12:00:00Z" })],
        }),
        revision({
          id: "r3",
          revision_number: 3,
          trigger: "document_processing",
          origin: "internal",
          events: [event("DOCUMENT_CONTENT_IDENTIFIED", { subject: PCAP_URL, old_value: null, new_value: "a".repeat(64), is_material: false })],
        }),
        revision({
          id: "r2",
          revision_number: 2,
          trigger: "document_intake",
          origin: "internal",
          events: [event("DOCUMENT_ROLE_REVIEWED", { subject: PCAP_URL, old_value: null, new_value: "pcap", is_material: false })],
        }),
        revision({ id: "r1", revision_number: 1, trigger: "ingestion", origin: "source", events: [event("TITLE_CHANGED", { old_value: "A", new_value: "B" })] }),
      ],
      DOCUMENTS,
    );
    for (const button of screen.queryAllByRole("button", { name: /Mostra/ })) fireEvent.click(button);
    const source = screen.getByRole("region", { name: "Publicat per la font" });
    const internal = screen.getByRole("region", { name: "Processament intern (Tuvi)" });
    expect(source.textContent).toContain("Versió 2 · Actualització de la font");
    expect(source.textContent).toContain("Versió 1 · Actualització de la font");
    expect(source.textContent).not.toContain("Versió 4");
    expect(internal.textContent).not.toMatch(/Versió/);
    expect(internal.textContent).toContain("Processament intern · En descarregar un document");
    expect(internal.textContent).toContain("Processament intern · En incorporar o revisar documents del paquet");
    expect(document.body.textContent ?? "").not.toMatch(/https?:|[a-z]+_[a-z_]+/);
  });
});

describe("TenderChangeHistory · codis nous amb valors reals (fix round 1, I1 i M6)", () => {
  function showAll() {
    for (const button of screen.queryAllByRole("button", { name: /Mostra/ })) fireEvent.click(button);
  }

  it("cap codi cru ni cap URL: estats, rols, tipus i àlies en català, i el document pel nom", () => {
    renderHistory([revision({ id: "r2", revision_number: 2, trigger: "document_intake", origin: "internal", events: REAL_EVENTS })], DOCUMENTS);
    showAll();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/https?:/);
    expect(text).not.toMatch(/[a-z]+_[a-z_]+/);
    expect(text).toContain("Aportació de l'operador: pendent de verificació · Annex 3.pdf");
    expect(text).toContain("Aportació de l'operador: pendent de verificació → verificat · Annex 3.pdf");
    expect(text).toContain("Aportació de l'operador: pendent de verificació → rebutjat · Annex 3.pdf");
    expect(text).toContain("Aportació de l'operador: rebutjat → retirat · Annex 3.pdf");
    expect(text).toContain("àlies de PCAP.pdf · PCAP (còpia).pdf");
    expect(text).toContain("Tipus de document revisat per l'operador: quadre de característiques · PCAP.pdf");
    expect(text).toContain("plec de clàusules administratives (PCAP) → annex d'inventari");
    expect(text).toContain("Plec administratiu → Plec tècnic · PCAP.pdf");
    expect(text).toContain("Adreça d'un document modificada · PCAP (còpia).pdf");
    expect(text).toContain("PCAP.pdf → PCAP definitiu.pdf");
  });

  it("sense el nom del document, cap URL: es diu «un document»", () => {
    renderHistory([revision({ id: "r2", revision_number: 2, trigger: "document_intake", origin: "internal", events: REAL_EVENTS })]);
    showAll();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/https?:/);
    expect(text).toContain("àlies d'un altre document");
  });

  it("una revisió sense `origin` (API antiga) no desapareix: va al processament intern, i la font no afirma res", () => {
    const legacy = revision({ id: "r3", revision_number: 3, trigger: "ingestion", events: [event("DEADLINE_CHANGED", { old_value: "2026-10-20T12:00:00Z", new_value: "2026-10-27T12:00:00Z" })] });
    delete (legacy as Partial<TenderHistoryRevision>).origin;
    renderHistory([legacy, revision({ id: "r1", revision_number: 1 })]);
    const internal = screen.getByRole("region", { name: "Processament intern (Tuvi)" });
    expect(within(internal).getByText(/Termini de presentació modificat/)).toBeTruthy();
    expect(within(internal).getByText(/Origen no informat/)).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("la font no n'ha modificat cap dada");
  });
});
