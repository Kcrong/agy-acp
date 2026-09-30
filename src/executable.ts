import { accessSync, constants, realpathSync, statSync } from "node:fs";
import { posix, win32, type PlatformPath } from "node:path";

export interface ExecutableResolverOptions {
  readonly platform?: NodeJS.Platform;
  readonly isExecutable?: (candidate: string) => boolean;
  readonly realpath?: (candidate: string) => string;
}

export class AgyExecutableResolutionError extends Error {
  public readonly code = "AGY_EXECUTABLE_NOT_FOUND";

  public constructor() {
    super("Unable to resolve agy executable");
    this.name = "AgyExecutableResolutionError";
  }
}

export function resolveAgyExecutable(
  configured: string,
  env: NodeJS.ProcessEnv,
  options: ExecutableResolverOptions = {},
): string {
  if (configured.length === 0 || configured.includes("\0")) {
    throw new AgyExecutableResolutionError();
  }

  const platform = options.platform ?? process.platform;
  const path = platform === "win32" ? win32 : posix;
  const isExecutable =
    options.isExecutable ??
    ((candidate: string) => defaultIsExecutable(candidate, platform));
  const canonicalize = options.realpath ?? realpathSync;
  const candidates = hasPathSeparator(configured)
    ? absoluteCandidate(configured, platform, path)
    : pathCandidates(configured, env, platform, path);

  for (const candidate of candidates) {
    if (!isExecutable(candidate)) {
      continue;
    }
    try {
      return canonicalize(candidate);
    } catch {
      throw new AgyExecutableResolutionError();
    }
  }

  throw new AgyExecutableResolutionError();
}

function hasPathSeparator(value: string): boolean {
  return value.includes("/") || value.includes("\\");
}

function absoluteCandidate(
  configured: string,
  platform: NodeJS.Platform,
  path: PlatformPath,
): readonly string[] {
  if (!path.isAbsolute(configured)) {
    throw new AgyExecutableResolutionError();
  }
  if (platform === "win32" && !isWindowsBinary(configured, path)) {
    throw new AgyExecutableResolutionError();
  }
  return [configured];
}

function pathCandidates(
  command: string,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
  path: PlatformPath,
): readonly string[] {
  const pathValue = env.PATH ?? env.Path ?? env.path ?? "";
  const directories = pathValue
    .split(platform === "win32" ? ";" : ":")
    .filter((directory) => path.isAbsolute(directory));
  const suffixes = windowsSuffixes(command, env, platform, path);

  return directories.flatMap((directory) =>
    suffixes.map((suffix) => path.join(directory, `${command}${suffix}`)),
  );
}

function windowsSuffixes(
  command: string,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
  path: PlatformPath,
): readonly string[] {
  if (platform !== "win32") {
    return [""];
  }

  const extension = path.extname(command);
  if (extension.length > 0) {
    return isWindowsBinary(command, path) ? [""] : [];
  }

  const configured = (env.PATHEXT ?? ".EXE;.COM")
    .split(";")
    .map((value) => value.toUpperCase());
  return configured.filter((value) => value === ".EXE" || value === ".COM");
}

function isWindowsBinary(value: string, path: PlatformPath): boolean {
  const extension = path.extname(value).toUpperCase();
  return extension === ".EXE" || extension === ".COM";
}

function defaultIsExecutable(
  candidate: string,
  platform: NodeJS.Platform,
): boolean {
  try {
    if (!statSync(candidate).isFile()) {
      return false;
    }
    accessSync(
      candidate,
      platform === "win32" ? constants.F_OK : constants.X_OK,
    );
    return true;
  } catch {
    return false;
  }
}
