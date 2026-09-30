import {
  execFile,
  spawn as nodeSpawn,
  type ChildProcessWithoutNullStreams,
} from "node:child_process";
import { isAbsolute, win32 } from "node:path";

export interface AgyInvocationOptions {
  readonly cwd: string;
  readonly executable?: string;
  readonly executableArguments?: readonly string[];
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
  readonly detached: boolean;
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
  for (const argument of options.executableArguments ?? []) {
    if (argument.includes("\0")) {
      throw new AgyProcessConfigError("executableArguments");
    }
    args.push(argument);
  }
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
    detached: process.platform !== "win32",
    env: options.env ?? process.env,
    shell: false,
    stdio: "pipe",
    windowsHide: true,
  });
}

export interface AgyProcessTreeSignalOptions {
  readonly platform?: NodeJS.Platform;
  readonly signalGroup?: (pid: number, signal: NodeJS.Signals) => void;
  readonly signalWindowsTree?: (pid: number, force: boolean) => void;
}

export function signalAgyProcessTree(
  child: ChildProcessWithoutNullStreams,
  signal: NodeJS.Signals,
  options: AgyProcessTreeSignalOptions = {},
): void {
  const pid = child.pid;
  if (pid === undefined || pid <= 0) {
    child.kill(signal);
    return;
  }

  try {
    if ((options.platform ?? process.platform) === "win32") {
      (options.signalWindowsTree ?? defaultSignalWindowsTree)(
        pid,
        signal === "SIGKILL",
      );
    } else {
      (options.signalGroup ?? process.kill)(-pid, signal);
    }
  } catch {
    child.kill(signal);
  }
}

function defaultSignalWindowsTree(pid: number, force: boolean): void {
  const systemRoot = process.env.SystemRoot ?? "C:\\Windows";
  const command = win32.join(systemRoot, "System32", "taskkill.exe");
  const args = ["/PID", String(pid), "/T"];
  if (force) {
    args.push("/F");
  }

  const killer = execFile(
    command,
    args,
    { windowsHide: true },
    () => undefined,
  );
  killer.unref();
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
