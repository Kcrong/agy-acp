import {
  spawn as nodeSpawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { isAbsolute } from "node:path";

export interface AgyInvocationOptions {
  readonly cwd: string;
  readonly executable?: string;
  readonly conversationId?: string;
  readonly additionalDirectories?: readonly string[];
  readonly env?: NodeJS.ProcessEnv;
}

export interface AgyInvocation {
  readonly command: string;
  readonly args: readonly string[];
  readonly cwd: string;
}

export interface AgySpawnOptions {
  readonly cwd: string;
  readonly env: NodeJS.ProcessEnv;
  readonly shell: false;
  readonly stdio: "pipe";
  readonly windowsHide: true;
}

export type AgySpawnFunction = (
  command: string,
  args: readonly string[],
  options: AgySpawnOptions,
) => ChildProcessWithoutNullStreams;

export class AgyProcessConfigError extends Error {
  public readonly code = "INVALID_PROCESS_CONFIG";
  public readonly field: string;

  public constructor(field: string) {
    super(`Invalid agy process configuration field: ${field}`);
    this.name = "AgyProcessConfigError";
    this.field = field;
  }
}

const DEFAULT_SPAWN: AgySpawnFunction = (command, args, options) =>
  nodeSpawn(command, [...args], options);

export function buildAgyInvocation(
  options: AgyInvocationOptions,
): AgyInvocation {
  requireAbsolutePath(options.cwd, "cwd");

  const executable = options.executable ?? "agy";
  requireNonEmptyWithoutNull(executable, "executable");

  const args: string[] = [];
  for (const directory of options.additionalDirectories ?? []) {
    requireAbsolutePath(directory, "additionalDirectories");
    args.push(`--add-dir=${directory}`);
  }

  if (options.conversationId !== undefined) {
    requireNonEmptyWithoutNull(options.conversationId, "conversationId");
    args.push(`--conversation=${options.conversationId}`);
  }

  args.push(
    "--input-format=stream-json",
    "--output-format=stream-json",
    "--print-timeout=0s",
    "--print=",
  );

  return {
    command: executable,
    args,
    cwd: options.cwd,
  };
}

export function spawnAgyProcess(
  options: AgyInvocationOptions,
  spawn: AgySpawnFunction = DEFAULT_SPAWN,
): ChildProcessWithoutNullStreams {
  const invocation = buildAgyInvocation(options);

  return spawn(invocation.command, invocation.args, {
    cwd: invocation.cwd,
    env: options.env ?? process.env,
    shell: false,
    stdio: "pipe",
    windowsHide: true,
  });
}

function requireAbsolutePath(value: string, field: string): void {
  requireNonEmptyWithoutNull(value, field);
  if (!isAbsolute(value)) {
    throw new AgyProcessConfigError(field);
  }
}

function requireNonEmptyWithoutNull(value: string, field: string): void {
  if (value.length === 0 || value.includes("\0")) {
    throw new AgyProcessConfigError(field);
  }
}
