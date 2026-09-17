import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args) } };
});

import { ApiError } from "@/lib/api";

import { useResource } from "./hooks";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("useResource", () => {
  beforeEach(() => {
    get.mockReset();
  });

  it("exposa l'status HTTP d'un error de l'API, perquè un 404 es pugui explicar com a E3", async () => {
    get.mockRejectedValue(new ApiError(404, "not_found", "Tender not found"));
    const { result } = renderHook(() => useResource<{ id: string }>("/tenders/x"));
    await flush();
    expect(result.current.error).toBe("Tender not found");
    expect(result.current.status).toBe(404);
    expect(result.current.data).toBeNull();
  });

  it("status és null quan no hi ha error o l'error no ve de l'API", async () => {
    get.mockResolvedValue({ id: "t1" });
    const { result } = renderHook(() => useResource<{ id: string }>("/tenders/t1"));
    await flush();
    expect(result.current.status).toBeNull();
    expect(result.current.data).toEqual({ id: "t1" });
    get.mockRejectedValue(new Error("xarxa"));
    await act(async () => { await result.current.reload(); });
    expect(result.current.error).toBe("xarxa");
    expect(result.current.status).toBeNull();
  });
});
