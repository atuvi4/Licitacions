import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobRequest } from "@/lib/jobs";
import type { TenderPortalSync } from "@/lib/tenders";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args) } };
});

import { ApiError } from "@/lib/api";

import { PortalSyncBar, usePortalSync } from "./PortalSync";
import { portalSyncOutcome, portalSyncWaitText } from "./portalSyncText";

const ENDPOINT = "/companies/c1/tenders/t1/job-requests/sync-portal-documents";
const ACTIVE = "/companies/c1/tenders/t1/job-requests?job_type=sync_portal_documents&active=true&limit=1";

const fresh: TenderPortalSync = { available: true, reason: null, last_synced_at: null, retry_after_seconds: 0 };

function job(extra: Partial<JobRequest> = {}): JobRequest {
  return {
    id: "j1",
    job_type: "sync_portal_documents",
    tender_id: "t1",
    status: "accepted",
    outcome: null,
    reused_existing: false,
    result_run_id: null,
    error_code: null,
    requested_at: "2026-10-29T16:42:58Z",
    finished_at: null,
    progress: null,
    started: false,
    ...extra,
  };
}

function respond(map: Record<string, unknown>) {
  get.mockImplementation((path: string) => Promise.resolve(path in map ? map[path] : []));
}

const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

interface HarnessProps {
  portalSync: TenderPortalSync;
  canRequest: boolean;
  permissionKnown: boolean;
  /** Fals = la fitxa es torna a carregar (esquelet): la barra es desmunta, el hook no. */
  showBar: boolean;
  onChanged: () => Promise<unknown>;
}

/** Com la fitxa: el hook sempre, la barra només quan hi ha la pestanya. */
function Harness({ portalSync, canRequest, permissionKnown, showBar, onChanged }: HarnessProps) {
  const state = usePortalSync("c1", "t1", portalSync, onChanged);
  return showBar ? (
    <PortalSyncBar companyId="c1" portalSync={portalSync} state={state} canRequest={canRequest} permissionKnown={permissionKnown} />
  ) : (
    <p>esquelet</p>
  );
}

function renderBar(props: Partial<Omit<HarnessProps, "onChanged">> = {}) {
  const onChanged = vi.fn().mockResolvedValue(undefined);
  const base: HarnessProps = { portalSync: fresh, canRequest: true, permissionKnown: true, showBar: true, onChanged };
  const view = render(<Harness {...base} {...props} />);
  const rerender = (next: Partial<Omit<HarnessProps, "onChanged">>) => view.rerender(<Harness {...base} {...props} {...next} />);
  return { ...view, onChanged, rerender };
}

const button = () => screen.getByRole("button", { name: /Actualitza documents de la PSCP/ }) as HTMLButtonElement;

describe("Actualitza documents de la PSCP", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    get.mockReset();
    post.mockReset();
    respond({ [ACTIVE]: [] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sense cap sincronització: el botó es pot prémer i diu què fa", async () => {
    const { container } = renderBar();
    await settle();
    expect(container.querySelector("[data-portal-sync]")?.textContent).toContain("Portal de la PSCP");
    expect(screen.getByText("Encara no s'ha sincronitzat amb el portal.")).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it("prémer-lo demana la feina, en segueix l'estat i, en acabar, torna a carregar la fitxa", async () => {
    post.mockResolvedValue(job());
    const { onChanged } = renderBar();
    await settle();

    fireEvent.click(button());
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(ENDPOINT, {});
    expect(screen.getByText("En cua · pendent d'inici")).toBeTruthy();
    expect(button().disabled).toBe(true);
    expect(onChanged).not.toHaveBeenCalled();

    respond({ [ACTIVE]: [], "/companies/c1/job-requests/j1": job({ status: "succeeded", outcome: "executed", finished_at: "2026-10-29T16:43:09Z" }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Documents actualitzats amb el que publica el portal de la PSCP.")).toBeTruthy();
  });

  it("el resultat i l'espera nova es veuen encara que la fitxa s'hagi tornat a carregar (la barra es desmunta)", async () => {
    post.mockResolvedValue(job({ started: true }));
    respond({ [ACTIVE]: [], "/companies/c1/job-requests/j1": job({ status: "succeeded", outcome: "executed" }) });
    const { container, rerender } = renderBar();
    await settle();
    fireEvent.click(button());
    await settle();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    // La fitxa es torna a carregar: esquelet un moment i després la fitxa nova, amb l'espera del servidor.
    rerender({ showBar: false });
    expect(container.querySelector("[data-portal-sync]")).toBeNull();
    rerender({ showBar: true, portalSync: { ...fresh, last_synced_at: "2026-10-29T16:43:09Z", retry_after_seconds: 299 } });
    await settle();
    expect(screen.getByText("Documents actualitzats amb el que publica el portal de la PSCP.")).toBeTruthy();
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 5 minuts.");
    expect(button().disabled).toBe(true);
  });

  it("acabada fa poc segons la fitxa: no demana res, diu quant falta i es torna a obrir quan passa l'espera", async () => {
    const { container } = renderBar({
      portalSync: { ...fresh, last_synced_at: "2026-10-29T16:43:09Z", retry_after_seconds: 240 },
    });
    await settle();
    const wait = container.querySelector("[data-portal-sync-wait]")!;
    expect(wait.textContent).toContain("S'ha sincronitzat amb el portal fa poc");
    expect(wait.textContent).toContain("Es podrà tornar a demanar d'aquí a 4 minuts.");
    expect(button().disabled).toBe(true);
    fireEvent.click(button());
    expect(post).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 1 minut.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    expect(container.querySelector("[data-portal-sync-wait]")).toBeNull();
    expect(screen.getByText(/^Última sincronització amb el portal: /)).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it("si una altra sessió s'hi ha avançat (429), espera el que diu el servidor i porta la llista nova", async () => {
    post.mockRejectedValue(
      new ApiError(429, "portal_sync_too_soon", "fa molt poc", [], { retry_after_seconds: 90 }),
    );
    const { container, onChanged } = renderBar();
    await settle();

    fireEvent.click(button());
    await settle();
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/ja s'ha sincronitzat amb el portal fa poc: s'ha tornat a carregar la llista/)).toBeTruthy();
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 2 minuts.");
    expect(button().disabled).toBe(true);
    fireEvent.click(button());
    expect(post).toHaveBeenCalledTimes(1);
    // Cap codi intern al text.
    expect(container.textContent).not.toContain("portal_sync_too_soon");
  });

  it("una feina fallada ho diu en català i es pot tornar a demanar de seguida", async () => {
    post.mockResolvedValue(job({ started: true }));
    respond({ [ACTIVE]: [], "/companies/c1/job-requests/j1": job({ status: "failed", error_code: "portal_unavailable" }) });
    const { container, onChanged } = renderBar();
    await settle();
    fireEvent.click(button());
    await settle();
    expect(screen.getByText("Llegint el portal de la PSCP")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByText("El portal de la PSCP no ha respost. No s'ha canviat cap document.")).toBeTruthy();
    expect(container.textContent).not.toContain("portal_unavailable");
    expect(button().disabled).toBe(false);
  });

  it("en carregar la pàgina reprèn una sincronització que ja corria", async () => {
    respond({ [ACTIVE]: [job({ started: true })] });
    renderBar();
    await settle();
    expect(screen.getByText("Llegint el portal de la PSCP")).toBeTruthy();
    expect(button().disabled).toBe(true);
  });

  it("sense permís: el botó hi és, desactivat, amb la raó", async () => {
    renderBar({ canRequest: false });
    await settle();
    expect(button().disabled).toBe(true);
    expect(screen.getByText("Només qui té permís per processar plecs pot actualitzar els documents de la PSCP.")).toBeTruthy();
  });

  it("si la licitació no es pot sincronitzar, ho diu i no deixa prémer", async () => {
    renderBar({ portalSync: { ...fresh, available: false, reason: "portal_sync_disabled" } });
    await settle();
    expect(screen.getByText("La sincronització amb el portal de la PSCP no està activa.")).toBeTruthy();
    expect(button().disabled).toBe(true);
  });

  it("un rol que l'API refusa (403) rep el missatge del rol, no el de les anàlisis", async () => {
    post.mockRejectedValue(new ApiError(403, "permission_denied", "rol"));
    const { onChanged } = renderBar();
    await settle();
    fireEvent.click(button());
    await settle();
    expect(screen.getByText("El teu rol no pot demanar la sincronització amb el portal de la PSCP.")).toBeTruthy();
    expect(onChanged).not.toHaveBeenCalled();
    expect(button().disabled).toBe(false);
  });
});

describe("textos de la sincronització", () => {
  it("quant falta, arrodonit a l'alça al minut", () => {
    expect(portalSyncWaitText(30_000)).toBe("d'aquí a menys d'un minut");
    expect(portalSyncWaitText(60_000)).toBe("d'aquí a 1 minut");
    expect(portalSyncWaitText(61_000)).toBe("d'aquí a 2 minuts");
    expect(portalSyncWaitText(300_000)).toBe("d'aquí a 5 minuts");
  });

  it("el resultat d'una feina acabada; res mentre corre", () => {
    expect(portalSyncOutcome(null)).toBeNull();
    expect(portalSyncOutcome(job())).toBeNull();
    expect(portalSyncOutcome(job({ status: "succeeded", outcome: "reused" }))?.message).toBe(
      "El portal de la PSCP no té res de nou: els documents ja estaven al dia.",
    );
    expect(portalSyncOutcome(job({ status: "succeeded", outcome: "partial" }))?.detail).toContain("en veuràs l'error a la seva fila");
    expect(portalSyncOutcome(job({ status: "failed", error_code: "portal_sync_conflict" }))).toEqual({
      tone: "error",
      message: "Els documents han canviat mentre es sincronitzaven. Torna-ho a demanar.",
    });
  });
});
