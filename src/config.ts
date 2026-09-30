export interface RuntimeLimits {
  readonly maxLineBytes: number;
  readonly maxStderrBytes: number;
  readonly initTimeoutMs: number;
  readonly promptTimeoutMs: number;
  readonly cancelGraceMs: number;
  readonly hardKillGraceMs: number;
}

export const DEFAULT_LIMITS: Readonly<RuntimeLimits> = Object.freeze({
  maxLineBytes: 4 * 1024 * 1024,
  maxStderrBytes: 64 * 1024,
  initTimeoutMs: 15_000,
  promptTimeoutMs: 30 * 60 * 1_000,
  cancelGraceMs: 5_000,
  hardKillGraceMs: 2_000,
});
