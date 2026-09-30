import { describe, expect, it } from "vitest";

import {
  AgyExecutableResolutionError,
  resolveAgyExecutable,
} from "../../src/executable.js";

describe("resolveAgyExecutable", () => {
  it("canonicalizes an absolute executable path", () => {
    expect(
      resolveAgyExecutable("/opt/bin/agy", {}, {
        platform: "linux",
        isExecutable: (candidate) => candidate === "/opt/bin/agy",
        realpath: () => "/real/agy",
      }),
    ).toBe("/real/agy");
  });

  it("resolves a bare command only from absolute PATH entries", () => {
    const checked: string[] = [];

    expect(
      resolveAgyExecutable("agy", { PATH: "relative:/safe/bin:/other/bin" }, {
        platform: "linux",
        isExecutable: (candidate) => {
          checked.push(candidate);
          return candidate === "/other/bin/agy";
        },
        realpath: (candidate) => candidate,
      }),
    ).toBe("/other/bin/agy");
    expect(checked).toEqual(["/safe/bin/agy", "/other/bin/agy"]);
  });

  it("uses Path and executable extensions on Windows without cmd scripts", () => {
    const checked: string[] = [];

    expect(
      resolveAgyExecutable(
        "agy",
        { Path: "relative;C:\\Tools", PATHEXT: ".CMD;.EXE;.COM" },
        {
          platform: "win32",
          isExecutable: (candidate) => {
            checked.push(candidate);
            return candidate === "C:\\Tools\\agy.EXE";
          },
          realpath: (candidate) => candidate,
        },
      ),
    ).toBe("C:\\Tools\\agy.EXE");
    expect(checked).toEqual(["C:\\Tools\\agy.EXE"]);
  });

  it.each(["./agy", "tools/agy", "tools\\agy", "agy\0other"])(
    "rejects unsafe or unresolved configured value without echoing it: %s",
    (configured) => {
      let thrown: unknown;
      try {
        resolveAgyExecutable(configured, { PATH: "/bin" }, {
          platform: "linux",
          isExecutable: () => false,
          realpath: (candidate) => candidate,
        });
      } catch (error) {
        thrown = error;
      }

      expect(thrown).toBeInstanceOf(AgyExecutableResolutionError);
      if (!(thrown instanceof Error)) {
        throw new TypeError("expected AgyExecutableResolutionError");
      }
      expect(thrown.message).not.toContain(configured);
    },
  );
});
