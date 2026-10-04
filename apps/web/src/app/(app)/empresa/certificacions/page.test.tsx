import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UNACCENTED_LABELS, accentedVocabularyFixture, legacyCertificationFixture, snapshotFixture } from "@/test/knowledge-fixture";

/**
 * Smoke de la pàgina de certificacions amb el vocabulari del copy v3: les normes surten
 * amb el rètol curt de la web i, si la web no les coneix, amb el rètol de l'API tal com
 * és (amb accents). Dades sintètiques.
 */

const get = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { get: (...args: unknown[]) => get(...args), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", membership: { role: "owner", company: { id: "c1", legal_name: "Empresa Demo SL", trade_name: null } } }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import CertificationsPage from "./page";

const BASE = "/companies/c1";
const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Certificacions: normes del vocabulari amb accents (copy v3)", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockImplementation((path: string) => {
      if (path === `${BASE}/certifications`) return Promise.resolve([legacyCertificationFixture]);
      if (path === `${BASE}/knowledge/snapshot`) return Promise.resolve(snapshotFixture());
      if (path === `${BASE}/knowledge/vocabulary`) return Promise.resolve(accentedVocabularyFixture);
      return Promise.reject(new Error(`inesperat: ${path}`));
    });
  });

  it("el selector de norma mostra els rètols, i el de l'API amb accents per a una norma que la web no coneix", async () => {
    const { container } = render(<CertificationsPage />);
    await screen.findByText("ENS categoria mitjana");
    await flush();
    expect(container.textContent).not.toMatch(UNACCENTED_LABELS);

    fireEvent.click(screen.getByRole("button", { name: "Nova certificació" }));
    const dialog = screen.getByRole("dialog");
    const select = within(dialog).getByLabelText(/^Norma/) as HTMLSelectElement;
    const options = [...select.options].map((option) => option.textContent);
    expect(options).toEqual([
      "Sense norma del vocabulari",
      "ENS",
      "ISO 9001",
      "ISO 14001",
      "ISO/IEC 27001",
      "ISO 45001 (seguretat i salut a la feina)",
    ]);
    expect(dialog.textContent).not.toMatch(UNACCENTED_LABELS);
  });
});
