export interface RuntimeLimits {
  readonly maxLineBytes: number;
  readonly maxStderrBytes: number;
  readonly maxSessions: number;
  readonly initTimeoutMs: number;
  readonly promptTimeoutMs: number;
  readonly cancelGraceMs: number;
  readonly hardKillGraceMs: number;
}

export const DEFAULT_LIMITS: Readonly<RuntimeLimits> = Object.freeze({
  maxLineBytes: 4 * 1024 * 1024,
  maxStderrBytes: 64 * 1024,
  maxSessions: 16,
  initTimeoutMs: 15_000,
  promptTimeoutMs: 30 * 60 * 1_000,
  cancelGraceMs: 5_000,
  hardKillGraceMs: 2_000,
});

export type RuntimeLimitField = keyof RuntimeLimits;

export class RuntimeConfigError extends Error {
  public readonly field: RuntimeLimitField;

  public constructor(field: RuntimeLimitField) {
    super(`Invalid runtime limit: ${field}`);
    this.name = "RuntimeConfigError";
    this.field = field;
  }
}

const ENVIRONMENT_KEYS: Readonly<Record<RuntimeLimitField, string>> = {
  maxLineBytes: "AGY_ACP_MAX_LINE_BYTES",
  maxStderrBytes: "AGY_ACP_MAX_STDERR_BYTES",
  maxSessions: "AGY_ACP_MAX_SESSIONS",
  initTimeoutMs: "AGY_ACP_INIT_TIMEOUT_MS",
  promptTimeoutMs: "AGY_ACP_PROMPT_TIMEOUT_MS",
  cancelGraceMs: "AGY_ACP_CANCEL_GRACE_MS",
  hardKillGraceMs: "AGY_ACP_HARD_KILL_GRACE_MS",
};

export function runtimeLimitsFromEnv(
  env: NodeJS.ProcessEnv,
  defaults: RuntimeLimits = DEFAULT_LIMITS,
): RuntimeLimits {
  return {
    maxLineBytes: readPositiveInteger(env, "maxLineBytes", defaults),
    maxStderrBytes: readPositiveInteger(env, "maxStderrBytes", defaults),
    maxSessions: readPositiveInteger(env, "maxSessions", defaults),
    initTimeoutMs: readPositiveInteger(env, "initTimeoutMs", defaults),
    promptTimeoutMs: readPositiveInteger(env, "promptTimeoutMs", defaults),
    cancelGraceMs: readPositiveInteger(env, "cancelGraceMs", defaults),
    hardKillGraceMs: readPositiveInteger(env, "hardKillGraceMs", defaults),
  };
}

function readPositiveInteger(
  env: NodeJS.ProcessEnv,
  field: RuntimeLimitField,
  defaults: RuntimeLimits,
): number {
  const raw = env[ENVIRONMENT_KEYS[field]];
  if (raw === undefined) {
    return defaults[field];
  }
  if (!/^[1-9]\d*$/.test(raw)) {
    throw new RuntimeConfigError(field);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value)) {
    throw new RuntimeConfigError(field);
  }
  return value;
}
