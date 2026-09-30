import { describe, expect, it } from "vitest";

import { DEFAULT_LIMITS } from "../../src/config.js";

describe("DEFAULT_LIMITS", () => {
  it("uses bounded positive limits", () => {
    for (const value of Object.values(DEFAULT_LIMITS)) {
      expect(value).toBeGreaterThan(0);
      expect(Number.isSafeInteger(value)).toBe(true);
    }
  });

  it("matches the bridge lifecycle contract", () => {
    expect(DEFAULT_LIMITS).toEqual({
      maxLineBytes: 4 * 1024 * 1024,
      maxStderrBytes: 64 * 1024,
      initTimeoutMs: 15_000,
      promptTimeoutMs: 30 * 60 * 1_000,
      cancelGraceMs: 5_000,
      hardKillGraceMs: 2_000,
    });
  });
});
