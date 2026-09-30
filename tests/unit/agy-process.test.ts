import type { ChildProcessWithoutNullStreams } from "node:child_process";

import { describe, expect, it } from "vitest";

import {
  AgyProcessConfigError,
  buildAgyInvocation,
  signalAgyProcessTree,
  spawnAgyProcess,
} from "../../src/agy-process.js";
import type { AgySpawnFunction } from "../../src/agy-process.js";

describe("buildAgyInvocation", () => {
  it("builds the minimal stream-json invocation with attached values", () => {
    expect(buildAgyInvocation({ cwd: "/workspace" })).toEqual({
      command: "agy",
      args: [
        "--input-format=stream-json",
        "--output-format=stream-json",
        "--print-timeout=0s",
        "--print=",
      ],
      cwd: "/workspace",
    });
  });

  it("keeps conversation and directory values as literal argv entries", () => {
    const invocation = buildAgyInvocation({
      cwd: "/workspace;echo ignored",
      executable: "/opt/agy binary",
      executableArguments: ["/fixture script.mjs", "--fixed-mode"],
      conversationId: "id;echo ignored",
      additionalDirectories: ["/other root", "/value;echo ignored"],
    });

    expect(invocation).toEqual({
      command: "/opt/agy binary",
      args: [
        "/fixture script.mjs",
        "--fixed-mode",
        "--add-dir=/other root",
        "--add-dir=/value;echo ignored",
        "--conversation=id;echo ignored",
        "--input-format=stream-json",
        "--output-format=stream-json",
        "--print-timeout=0s",
        "--print=",
      ],
      cwd: "/workspace;echo ignored",
    });
    expect(invocation.args).not.toContain("--dangerously-skip-permissions");
  });

  it.each([
    [{ cwd: "relative/path" }, "cwd"],
    [{ cwd: "/workspace", additionalDirectories: ["relative/path"] }, "additionalDirectories"],
    [{ cwd: "/workspace", conversationId: "" }, "conversationId"],
    [{ cwd: "/workspace", executable: "agy\0other" }, "executable"],
    [{ cwd: "/workspace", executableArguments: ["bad\0argument"] }, "executableArguments"],
  ] as const)("rejects invalid launch configuration at %s", (input, field) => {
    expect(() => buildAgyInvocation(input)).toThrowError(
      expect.objectContaining<Partial<AgyProcessConfigError>>({
        code: "INVALID_PROCESS_CONFIG",
        field,
      }),
    );
  });
});

describe("spawnAgyProcess", () => {
  it("spawns without a shell and preserves the caller environment", () => {
    const child = {} as ChildProcessWithoutNullStreams;
    const calls: Parameters<AgySpawnFunction>[] = [];
    const spawn: AgySpawnFunction = (command, args, options) => {
      calls.push([command, args, options]);
      return child;
    };
    const env = { PATH: "/custom/bin" };

    expect(
      spawnAgyProcess(
        {
          cwd: "/workspace",
          executable: "/custom/agy",
          env,
        },
        spawn,
      ),
    ).toBe(child);
    expect(calls).toEqual([
      [
        "/custom/agy",
        [
          "--input-format=stream-json",
          "--output-format=stream-json",
          "--print-timeout=0s",
          "--print=",
        ],
        {
          cwd: "/workspace",
          detached: true,
          env,
          shell: false,
          stdio: "pipe",
          windowsHide: true,
        },
      ],
    ]);
  });
});

describe("signalAgyProcessTree", () => {
  it("signals the detached POSIX process group", () => {
    const directSignals: NodeJS.Signals[] = [];
    const groupSignals: Array<[number, NodeJS.Signals]> = [];
    const child = {
      pid: 4321,
      kill(signal: NodeJS.Signals) {
        directSignals.push(signal);
        return true;
      },
    } as ChildProcessWithoutNullStreams;

    signalAgyProcessTree(child, "SIGTERM", {
      platform: "linux",
      signalGroup: (pid, signal) => groupSignals.push([pid, signal]),
    });

    expect(groupSignals).toEqual([[-4321, "SIGTERM"]]);
    expect(directSignals).toEqual([]);
  });

  it("uses the Windows tree terminator with force only for SIGKILL", () => {
    const calls: Array<[number, boolean]> = [];
    const child = { pid: 1234, kill: () => true } as ChildProcessWithoutNullStreams;
    const options = {
      platform: "win32" as const,
      signalWindowsTree: (pid: number, force: boolean) =>
        calls.push([pid, force]),
    };

    signalAgyProcessTree(child, "SIGTERM", options);
    signalAgyProcessTree(child, "SIGKILL", options);

    expect(calls).toEqual([
      [1234, false],
      [1234, true],
    ]);
  });
});
