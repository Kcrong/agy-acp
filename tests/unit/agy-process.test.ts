import type { ChildProcessWithoutNullStreams } from "node:child_process";

import { describe, expect, it } from "vitest";

import {
  AgyProcessConfigError,
  buildAgyInvocation,
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
      conversationId: "id;echo ignored",
      additionalDirectories: ["/other root", "/value;echo ignored"],
    });

    expect(invocation).toEqual({
      command: "/opt/agy binary",
      args: [
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
          env,
          shell: false,
          stdio: "pipe",
          windowsHide: true,
        },
      ],
    ]);
  });
});
