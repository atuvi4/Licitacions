import { afterEach, describe, expect, it, vi } from "vitest";

import { SecureRandomUnavailableError, browserUuid, formatUuidV4 } from "./uuid";

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** Bytes deterministes: cada crida omple amb `seed`, `seed+1`, ... */
function fakeRandom(seeds: number[]) {
  return vi.fn((array: Uint8Array) => {
    const seed = seeds.shift() ?? 0;
    array.forEach((_, i) => {
      array[i] = (seed + i) & 0xff;
    });
    return array;
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("browserUuid", () => {
  it("fa servir randomUUID quan el navegador l'ofereix (context segur)", () => {
    const randomUUID = vi.fn(() => "11111111-1111-4111-8111-111111111111");
    const getRandomValues = vi.fn();
    vi.stubGlobal("crypto", { randomUUID, getRandomValues });

    expect(browserUuid()).toBe("11111111-1111-4111-8111-111111111111");
    expect(randomUUID).toHaveBeenCalledOnce();
    expect(getRandomValues).not.toHaveBeenCalled();
  });

  it("sense randomUUID (HTTP a la xarxa local) genera un v4 amb getRandomValues", () => {
    const getRandomValues = fakeRandom([0xf0]);
    vi.stubGlobal("crypto", { getRandomValues });

    const value = browserUuid();

    expect(value).toMatch(V4);
    expect(getRandomValues).toHaveBeenCalledOnce();
    expect(getRandomValues.mock.calls[0][0]).toHaveLength(16);
  });

  it("dues crides sense randomUUID donen identificadors diferents", () => {
    vi.stubGlobal("crypto", { getRandomValues: fakeRandom([1, 2]) });

    const first = browserUuid();
    const second = browserUuid();

    expect(first).toMatch(V4);
    expect(second).toMatch(V4);
    expect(first).not.toBe(second);
  });

  it("amb el generador real i sense randomUUID continuen sent v4 i diferents", () => {
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", { getRandomValues: real.getRandomValues.bind(real) });

    const values = new Set(Array.from({ length: 200 }, () => browserUuid()));

    expect(values.size).toBe(200);
    for (const value of values) expect(value).toMatch(V4);
  });

  it("sense cap generador criptogràfic falla explícitament, mai amb Math.random", () => {
    const random = vi.spyOn(Math, "random");
    vi.stubGlobal("crypto", undefined);
    expect(() => browserUuid()).toThrow(SecureRandomUnavailableError);
    vi.stubGlobal("crypto", {});
    expect(() => browserUuid()).toThrow(SecureRandomUnavailableError);
    expect(random).not.toHaveBeenCalled();
    random.mockRestore();
  });
});

describe("formatUuidV4", () => {
  it("fixa els bits de versió (4) i de variant (10) i conserva la resta", () => {
    const ones = formatUuidV4(new Uint8Array(16).fill(0xff));
    const zeros = formatUuidV4(new Uint8Array(16));

    expect(ones).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(zeros).toBe("00000000-0000-4000-8000-000000000000");
  });

  it("no modifica els bytes d'entrada", () => {
    const bytes = new Uint8Array(16).fill(0xff);
    formatUuidV4(bytes);
    expect(Array.from(bytes)).toEqual(new Array(16).fill(0xff));
  });

  it("rebutja una mida que no sigui 16 bytes", () => {
    expect(() => formatUuidV4(new Uint8Array(15))).toThrow(RangeError);
  });
});
