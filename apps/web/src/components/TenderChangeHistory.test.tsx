import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TenderChangeHistory } from "./TenderChangeHistory";

describe("TenderChangeHistory", () => {
  it("no mostra camps interns, booleans ni URLs sota els canvis de presència i de documents", () => {
    const url = "https://contrataciondelestado.es/wps/download/abc";
    const events = [
      { change_code: "SOURCE_ENTRY_RESTORED", subject: "source_removed", old_value: true, new_value: false, impacts: ["informational"], is_material: true, detected_at: "2026-10-06T10:00:00Z" },
      { change_code: "DOCUMENT_REMOVED", subject: url, old_value: "Plec.pdf", new_value: null, impacts: ["informational"], is_material: true, detected_at: "2026-10-06T10:00:00Z" },
    ] as never;
    const { container } = render(
      <TenderChangeHistory
        revisions={[{ id: "r1", revision_number: 2, trigger: "ingestion", created_at: "2026-10-06T10:00:00Z", change_summary: null, events }]}
        loading={false}
        error={null}
        onReload={() => undefined}
      />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Torna a estar disponible a la font");
    expect(text).toContain("Document retirat: Plec.pdf");
    expect(text).not.toMatch(/source_removed|true|false|https?:/);
  });
});
