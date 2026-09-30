import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AgyProcessController,
  AgyProcessControllerError,
} from "../../src/process-controller.js";
import type { AgySpawnFunction } from "../../src/agy-process.js";
import type { RuntimeLimits } from "../../src/config.js";

const LIMITS: RuntimeLimits = {
  maxLineBytes: 1_024,
  maxStderrBytes: 8,
  initTimeoutMs: 100,
  promptTimeoutMs: 1_000,
  cancelGraceMs: 50,
  hardKillGraceMs: 50,
};

interface FakeProcess {
  readonly child: ChildProcessWithoutNullStreams;
  readonly stdin: Writable;
  readonly stdout: PassThrough;
  readonly stderr: PassThrough;
  readonly signals: NodeJS.Signals[];
  emitExit(code?: number | null, signal?: NodeJS.Signals | null): void;
}

function createFakeProcess(stdin: Writable = new PassThrough()): FakeProcess {
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const emitter = new EventEmitter();
  const signals: NodeJS.Signals[] = [];

  Object.assign(emitter, {
    stdin,
    stdout,
    stderr,
    kill(signal: NodeJS.Signals = "SIGTERM") {
      signals.push(signal);
      return true;
    },
  });

  return {
    child: emitter as ChildProcessWithoutNullStreams,
    stdin,
    stdout,
    stderr,
    signals,
    emitExit(code = 0, signal = null) {
      emitter.emit("exit", code, signal);
    },
  };
}

function spawnReturning(fake: FakeProcess): AgySpawnFunction {
  return () => fake.child;
}

function initLine(id = "opaque-id"): string {
  return `${JSON.stringify({
    event: "init",
    conversation_id: id,
    init: {
      cwd: "/workspace",
      permission_mode: "default",
      tools: [],
    },
  })}\n`;
}

async function startReady(fake: FakeProcess, overrides?: Partial<RuntimeLimits>) {
  const started = AgyProcessController.start(
    {
      invocation: { cwd: "/workspace" },
      limits: { ...LIMITS, ...overrides },
    },
    spawnReturning(fake),
  );
  fake.stdout.write(initLine());
  return started;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("AgyProcessController", () => {
  it("waits for a split init event and exposes only the opaque id", async () => {
    const fake = createFakeProcess();
    const started = AgyProcessController.start(
      {
        invocation: { cwd: "/workspace" },
        limits: LIMITS,
      },
      spawnReturning(fake),
    );
    const line = Buffer.from(initLine("conversation-id"));

    fake.stdout.write(line.subarray(0, 9));
    fake.stdout.write(line.subarray(9));

    const controller = await started;
    expect(controller.conversationId).toBe("conversation-id");
  });

  it("parses and dispatches typed events after init", async () => {
    const fake = createFakeProcess();
    const events: string[] = [];
    const controller = await startReady(fake);
    controller.onEvent((event) => events.push(event.kind));

    fake.stdout.write(
      `${JSON.stringify({
        event: "step_update",
        step_update: {
          conversation_id: "opaque-id",
          step_index: 0,
          state: "running",
          step_type: "assistant_text",
          text_delta: "hello",
        },
      })}\n${JSON.stringify({ event: "future_event" })}\n`,
    );

    expect(events).toEqual(["step_update", "unknown"]);
  });

  it("fails once on malformed stdout after init", async () => {
    const fake = createFakeProcess();
    const failures: AgyProcessControllerError[] = [];
    const controller = await startReady(fake);
    controller.onFailure((error) => failures.push(error));

    fake.stdout.write("{broken}\n");
    fake.stdout.write("{broken-again}\n");

    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ code: "INVALID_OUTPUT" });
    expect(fake.signals).toEqual(["SIGTERM"]);
  });

  it("rejects and terminates when init times out", async () => {
    vi.useFakeTimers();
    const fake = createFakeProcess();
    const started = AgyProcessController.start(
      {
        invocation: { cwd: "/workspace" },
        limits: { ...LIMITS, initTimeoutMs: 10 },
      },
      spawnReturning(fake),
    );
    const assertion = expect(started).rejects.toMatchObject({
      code: "INIT_TIMEOUT",
    });

    await vi.advanceTimersByTimeAsync(10);
    await assertion;
    expect(fake.signals).toEqual(["SIGTERM"]);
  });

  it("rejects when the process exits before init", async () => {
    const fake = createFakeProcess();
    const started = AgyProcessController.start(
      {
        invocation: { cwd: "/workspace" },
        limits: LIMITS,
      },
      spawnReturning(fake),
    );

    fake.emitExit(7);

    await expect(started).rejects.toMatchObject({ code: "PROCESS_EXITED" });
  });

  it("bounds stderr without exposing its contents", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake, { maxStderrBytes: 4 });

    fake.stderr.write("sensitive-diagnostic");

    expect(controller.diagnostics).toEqual({
      stderrBytes: 4,
      stderrTruncated: true,
    });
    expect(JSON.stringify(controller.diagnostics)).not.toContain("sensitive");
  });

  it("waits for drain when stdin applies backpressure", async () => {
    class ControlledWritable extends Writable {
      public written = "";
      #release: (() => void) | undefined;

      public constructor() {
        super({ highWaterMark: 1 });
      }

      public release(): void {
        this.#release?.();
      }

      public override _write(
        chunk: Buffer,
        _encoding: BufferEncoding,
        callback: (error?: Error | null) => void,
      ): void {
        this.written += chunk.toString("utf8");
        this.#release = callback;
      }
    }

    const stdin = new ControlledWritable();
    const fake = createFakeProcess(stdin);
    const controller = await startReady(fake);
    let settled = false;

    const sending = controller
      .send({ event: "user", message: { content: [] } })
      .then(() => {
        settled = true;
      });
    await Promise.resolve();

    expect(settled).toBe(false);
    expect(stdin.written).toBe(
      '{"event":"user","message":{"content":[]}}\n',
    );

    stdin.release();
    await sending;
    expect(settled).toBe(true);
  });
});
