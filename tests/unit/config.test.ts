import { describe, expect, it } from "vitest";

import {
  DEFAULT_LIMITS,
  RuntimeConfigError,
  runtimeLimitsFromEnv,
} from "../../src/config.js";

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
      maxSessions: 16,
      initTimeoutMs: 15_000,
      promptTimeoutMs: 30 * 60 * 1_000,
      cancelGraceMs: 5_000,
      hardKillGraceMs: 2_000,
    });
  });
});

describe("runtimeLimitsFromEnv", () => {
  it("overrides only provided positive integer limits", () => {
    expect(
      runtimeLimitsFromEnv({
        AGY_ACP_PROMPT_TIMEOUT_MS: "25",
        AGY_ACP_CANCEL_GRACE_MS: "10",
      }),
    ).toEqual({
      ...DEFAULT_LIMITS,
      promptTimeoutMs: 25,
      cancelGraceMs: 10,
    });
  });

  it("rejects invalid values without echoing them", () => {
    const sensitiveValue = "invalid-secret-like-value";
    let thrown: unknown;

    try {
      runtimeLimitsFromEnv({ AGY_ACP_PROMPT_TIMEOUT_MS: sensitiveValue });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(RuntimeConfigError);
    if (!(thrown instanceof Error)) {
      throw new TypeError("expected RuntimeConfigError");
    }
    expect(thrown.message).not.toContain(sensitiveValue);
  });
});
