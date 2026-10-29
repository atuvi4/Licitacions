import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { JobRequest } from "@/lib/jobs";
import { formatDateTime, type TenderPortalSync } from "@/lib/tenders";

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
const OWN = "/companies/c1/tenders/t1/job-requests?job_type=sync_portal_documents&limit=20";

const fresh: TenderPortalSync = { available: true, reason: null, retry_after_seconds: 0 };

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
  const all: Record<string, unknown> = { [ACTIVE]: [], [OWN]: [], ...map };
  get.mockImplementation((path: string) => Promise.resolve(path in all ? all[path] : []));
}

const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

interface HarnessProps {
  portalSync: TenderPortalSync;
  canRequest: boolean;
  permissionKnown: boolean;
  /** Fals = s'ha canviat de pestanya: la barra es desmunta, el hook (a la fitxa) no. */
  showBar: boolean;
  onChanged: () => Promise<unknown>;
}

/** Com la fitxa: el hook sempre, la barra només quan hi ha la pestanya Plecs. */
function Harness({ portalSync, canRequest, permissionKnown, showBar, onChanged }: HarnessProps) {
  const state = usePortalSync("c1", "t1", portalSync, onChanged);
  return showBar ? (
    <PortalSyncBar companyId="c1" portalSync={portalSync} state={state} canRequest={canRequest} permissionKnown={permissionKnown} />
  ) : (
    <p>una altra pestanya</p>
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
const result = (container: HTMLElement) => container.querySelector("[data-portal-sync-result]")!;

describe("Actualitza documents de la PSCP", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    get.mockReset();
    post.mockReset();
    respond({});
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("sense cap sincronització de l'empresa: el botó es pot prémer i diu què fa", async () => {
    const { container } = renderBar();
    await settle();
    expect(container.querySelector("[data-portal-sync]")?.textContent).toContain("Portal de la PSCP");
    expect(screen.getByText("La vostra empresa encara no n'ha demanat cap actualització.")).toBeTruthy();
    expect(container.textContent).not.toMatch(/\bfeed\b/);
    expect(button().disabled).toBe(false);
  });

  it("la data és la de l'última que ha acabat bé de L'EMPRESA (les seves peticions)", async () => {
    respond({
      [OWN]: [
        job({ id: "j3", status: "failed", error_code: "portal_unavailable", finished_at: "2026-10-29T10:00:00Z" }),
        job({ id: "j2", status: "succeeded", outcome: "executed", finished_at: "2026-10-28T09:30:00Z" }),
      ],
    });
    renderBar();
    await settle();
    expect(get).toHaveBeenCalledWith(OWN);
    expect(
      screen.getByText(`Última actualització demanada per la vostra empresa: ${formatDateTime("2026-10-28T09:30:00Z")}.`),
    ).toBeTruthy();
  });

  it("prémer-lo demana la feina, en segueix l'estat i, en acabar, torna a carregar la fitxa i ho anuncia", async () => {
    post.mockResolvedValue(job());
    const { container, onChanged } = renderBar();
    await settle();

    fireEvent.click(button());
    await settle();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(ENDPOINT, {});
    expect(screen.getByText("En cua · pendent d'inici")).toBeTruthy();
    expect(button().disabled).toBe(true);
    expect(onChanged).not.toHaveBeenCalled();

    respond({ "/companies/c1/job-requests/j1": job({ status: "succeeded", outcome: "executed", finished_at: "2026-10-29T16:43:09Z" }) });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
    const region = result(container);
    expect(region.getAttribute("role")).toBe("status");
    expect(region.textContent).toContain("Documents actualitzats amb el que publica el portal de la PSCP.");
    expect(
      screen.getByText(`Última actualització demanada per la vostra empresa: ${formatDateTime("2026-10-29T16:43:09Z")}.`),
    ).toBeTruthy();
  });

  it("el resultat es conserva si es canvia de pestanya mentre corre, i torna amb l'espera nova", async () => {
    post.mockResolvedValue(job({ started: true }));
    respond({ "/companies/c1/job-requests/j1": job({ status: "succeeded", outcome: "executed", finished_at: "2026-10-29T16:43:09Z" }) });
    const { container, rerender } = renderBar();
    await settle();
    fireEvent.click(button());
    await settle();
    rerender({ showBar: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(container.querySelector("[data-portal-sync]")).toBeNull();
    // Torna a Plecs amb la fitxa nova, que porta l'espera del servidor.
    rerender({ showBar: true, portalSync: { ...fresh, retry_after_seconds: 299 } });
    await settle();
    expect(result(container).textContent).toContain("Documents actualitzats amb el que publica el portal de la PSCP.");
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 5 minuts.");
    expect(button().disabled).toBe(true);
  });

  it("acabada fa poc segons la fitxa: desactivat des del primer instant, sense dir quan ni qui, i es torna a obrir", async () => {
    const { container } = renderBar({ portalSync: { ...fresh, retry_after_seconds: 240 } });
    // Cap fotograma amb el botó actiu: ja ho és abans de cap efecte.
    expect(button().disabled).toBe(true);
    await settle();
    const wait = container.querySelector("[data-portal-sync-wait]")!;
    expect(wait.textContent).toBe(
      "Aquesta licitació s'ha sincronitzat amb el portal fa poc. Es podrà tornar a demanar d'aquí a 4 minuts.",
    );
    // El compte enrere no és a la regió que s'anuncia.
    expect(result(container).contains(wait)).toBe(false);
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
    expect(screen.getByText("La vostra empresa encara no n'ha demanat cap actualització.")).toBeTruthy();
    expect(button().disabled).toBe(false);
  });

  it("si una altra sessió s'hi ha avançat (429), porta la llista nova, ho diu i espera el que diu el servidor", async () => {
    post.mockRejectedValue(new ApiError(429, "portal_sync_too_soon", "fa molt poc", [], { retry_after_seconds: 90 }));
    const { container, onChanged } = renderBar();
    await settle();

    fireEvent.click(button());
    await settle();
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(result(container).textContent).toMatch(/ja s'ha sincronitzat amb el portal fa poc: s'ha tornat a carregar la llista/);
    expect(container.querySelector("[data-portal-sync-wait]")?.textContent).toContain("d'aquí a 2 minuts.");
    expect(button().disabled).toBe(true);
    fireEvent.click(button());
    expect(post).toHaveBeenCalledTimes(1);
    // Cap codi intern al text.
    expect(container.textContent).not.toContain("portal_sync_too_soon");
  });

  it("una feina fallada ho diu en català i es pot tornar a demanar de seguida", async () => {
    post.mockResolvedValue(job({ started: true }));
    respond({ "/companies/c1/job-requests/j1": job({ status: "failed", error_code: "portal_unavailable" }) });
    const { container, onChanged } = renderBar();
    await settle();
    fireEvent.click(button());
    await settle();
    expect(screen.getByText("Llegint el portal de la PSCP")).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(result(container).textContent).toContain("El portal de la PSCP no ha respost. No s'ha canviat cap document.");
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
