import type { ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import { PassThrough, Writable } from "node:stream";
import { setImmediate as immediate } from "node:timers/promises";

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
  maxSessions: 16,
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
  emitExitOnly(code?: number | null, signal?: NodeJS.Signals | null): void;
  emitClose(code?: number | null, signal?: NodeJS.Signals | null): void;
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
      emitter.emit("close", code, signal);
    },
    emitExitOnly(code = 0, signal = null) {
      emitter.emit("exit", code, signal);
    },
    emitClose(code = 0, signal = null) {
      emitter.emit("close", code, signal);
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

function resultLine(status: "SUCCESS" | "ERROR" = "SUCCESS"): string {
  return `${JSON.stringify({
    event: "result",
    result: {
      conversation_id: "opaque-id",
      duration_seconds: 1,
      error: status === "ERROR" ? "failed" : null,
      num_turns: 1,
      response: "result",
      status,
      usage: {},
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
    controller.onEvent((event) => {
      events.push(event.kind);
    });
    const turn = controller.runTurn({ event: "user" });

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
    await immediate();

    expect(events).toEqual(["step_update", "unknown"]);
    fake.stdout.write(resultLine());
    await turn;
  });

  it("fails once on malformed stdout after init", async () => {
    const fake = createFakeProcess();
    const failures: AgyProcessControllerError[] = [];
    const controller = await startReady(fake);
    controller.onFailure((error) => failures.push(error));

    fake.stdout.write("{broken}\n");
    fake.stdout.write("{broken-again}\n");
    await immediate();

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
    expect(fake.signals).toEqual(["SIGTERM"]);
    fake.emitExit(null, "SIGTERM");
    await vi.advanceTimersByTimeAsync(LIMITS.hardKillGraceMs);
    await assertion;
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

describe("AgyProcessController turn lifecycle", () => {
  it("resolves a turn with the terminal result", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);

    const turn = controller.runTurn({ event: "user" });
    fake.stdout.write(resultLine());

    await expect(turn).resolves.toMatchObject({
      kind: "result",
      status: "SUCCESS",
      response: "result",
    });
  });

  it("rejects overlapping turns for one process", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);

    const first = controller.runTurn({ event: "user" });
    await expect(controller.runTurn({ event: "user" })).rejects.toMatchObject({
      code: "PROCESS_BUSY",
    });

    fake.stdout.write(resultLine());
    await first;
  });

  it("times out a prompt and escalates from SIGTERM to SIGKILL", async () => {
    vi.useFakeTimers();
    const fake = createFakeProcess();
    const controller = await startReady(fake, {
      promptTimeoutMs: 10,
      cancelGraceMs: 20,
      hardKillGraceMs: 30,
    });

    const turn = controller.runTurn({ event: "user" });
    const assertion = expect(turn).rejects.toMatchObject({
      code: "PROMPT_TIMEOUT",
    });

    await vi.advanceTimersByTimeAsync(10);
    await assertion;
    expect(fake.signals).toEqual(["SIGTERM"]);

    await vi.advanceTimersByTimeAsync(20);
    expect(fake.signals).toEqual(["SIGTERM", "SIGKILL"]);

    await vi.advanceTimersByTimeAsync(30);
    expect(fake.child.listenerCount("exit")).toBe(0);
  });

  it("cancels an active turn and settles after process exit", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    const turn = controller.runTurn({ event: "user" });
    const turnAssertion = expect(turn).rejects.toMatchObject({
      code: "CANCELLED",
    });

    const cancelling = controller.cancel();
    expect(fake.signals).toEqual(["SIGTERM"]);
    await turnAssertion;

    fake.emitExit(null, "SIGTERM");
    await cancelling;
  });

  it("closes idempotently and removes process and stream listeners", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);

    const first = controller.close();
    const second = controller.close();

    expect(second).toBe(first);
    expect(fake.stdin.writableEnded).toBe(true);
    expect(fake.signals).toEqual(["SIGTERM"]);

    fake.emitExit(0);
    await first;

    expect(fake.child.listenerCount("error")).toBe(0);
    expect(fake.child.listenerCount("exit")).toBe(0);
    expect(fake.stdout.listenerCount("data")).toBe(0);
    expect(fake.stdout.listenerCount("end")).toBe(0);
    expect(fake.stderr.listenerCount("data")).toBe(0);
  });
});

describe("AgyProcessController closed cleanup", () => {
  it("closes immediately after an unexpected process exit", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);

    fake.emitExit(7);

    await expect(controller.close()).resolves.toBeUndefined();
    expect(fake.signals).toEqual([]);
    expect(fake.child.listenerCount("exit")).toBe(0);
  });
});

function stepLine(stepIndex: number, text: string): string {
  return `${JSON.stringify({
    event: "step_update",
    step_update: {
      conversation_id: "opaque-id",
      step_index: stepIndex,
      state: "running",
      step_type: "assistant_text",
      text_delta: text,
    },
  })}\n`;
}

describe("AgyProcessController output backpressure", () => {
  it("pauses child stdout while an async event listener is pending", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    const seen: number[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });

    controller.onEvent(async (event) => {
      if (event.kind !== "step_update") {
        return;
      }
      seen.push(event.stepIndex);
      if (event.stepIndex === 0) {
        await gate;
      }
    });
    const turn = controller.runTurn({ event: "user" });

    fake.stdout.write(stepLine(0, "first"));
    await immediate();
    expect(fake.stdout.isPaused()).toBe(true);

    fake.stdout.write(stepLine(1, "second"));
    await immediate();
    expect(seen).toEqual([0]);

    release();
    await immediate();
    await immediate();
    expect(seen).toEqual([0, 1]);
    expect(fake.stdout.isPaused()).toBe(false);
    fake.stdout.write(resultLine());
    await turn;
  });

  it("handles listener rejection immediately and terminates once", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    const failures: AgyProcessControllerError[] = [];
    controller.onFailure((error) => failures.push(error));
    controller.onEvent(() => Promise.reject(new Error("downstream failed")));
    const turn = controller.runTurn({ event: "user" });
    const turnAssertion = expect(turn).rejects.toMatchObject({
      code: "EVENT_HANDLER_FAILED",
    });

    fake.stdout.write(stepLine(0, "text"));
    await immediate();
    await immediate();
    await turnAssertion;

    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ code: "EVENT_HANDLER_FAILED" });
    expect(fake.signals).toEqual(["SIGTERM"]);
  });
});

describe("AgyProcessController terminal ordering", () => {
  it("fails an active turn immediately when stdout ends without a result", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    const turn = controller.runTurn({ event: "user" });

    fake.stdout.end();

    await expect(turn).rejects.toMatchObject({ code: "INVALID_OUTPUT" });
    expect(fake.signals).toEqual(["SIGTERM"]);
  });

  it("drains queued stdout after exit before handling child close", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    controller.onEvent(async (event) => {
      if (event.kind === "step_update") {
        await gate;
      }
    });

    const turn = controller.runTurn({ event: "user" });
    fake.stdout.write(`${stepLine(0, "text")}${resultLine()}`);
    await immediate();
    fake.emitExitOnly(0);

    release();
    await expect(turn).resolves.toMatchObject({ status: "SUCCESS" });
    fake.stdout.end();
    fake.emitClose(0);
  });

  it("retires the controller after stdin write failure", async () => {
    class FailingWritable extends Writable {
      public override _write(
        _chunk: Buffer,
        _encoding: BufferEncoding,
        callback: (error?: Error | null) => void,
      ): void {
        callback(new Error("write failed"));
      }
    }

    const fake = createFakeProcess(new FailingWritable({ highWaterMark: 1 }));
    const controller = await startReady(fake);
    const failures: AgyProcessControllerError[] = [];
    controller.onFailure((error) => failures.push(error));

    await expect(controller.runTurn({ event: "user" })).rejects.toMatchObject({
      code: "WRITE_FAILED",
    });
    await immediate();

    expect(controller.isClosed).toBe(true);
    expect(failures).toHaveLength(1);
    expect(fake.signals).toEqual(["SIGTERM"]);
  });

  it("rejects a result received while no turn is active", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    const failures: AgyProcessControllerError[] = [];
    controller.onFailure((error) => failures.push(error));

    fake.stdout.write(resultLine());
    await immediate();
    await immediate();

    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ code: "INVALID_OUTPUT" });
    expect(fake.signals).toEqual(["SIGTERM"]);
  });
});

describe("AgyProcessController high-severity barriers", () => {
  it("handles an asynchronous stdin EPIPE after write returned true", async () => {
    class AsyncEpipeWritable extends Writable {
      public override _write(
        _chunk: Buffer,
        _encoding: BufferEncoding,
        callback: (error?: Error | null) => void,
      ): void {
        setImmediate(() => {
          const error = new Error("simulated EPIPE");
          this.emit("error", error);
          callback(error);
        });
      }
    }

    const stdin = new AsyncEpipeWritable({ highWaterMark: 1_024 });
    const fake = createFakeProcess(stdin);
    const controller = await startReady(fake);
    const failures: AgyProcessControllerError[] = [];
    controller.onFailure((error) => failures.push(error));

    await expect(controller.runTurn({ event: "user" })).rejects.toMatchObject({
      code: "WRITE_FAILED",
    });
    await immediate();

    expect(failures).toHaveLength(1);
    expect(controller.isClosed).toBe(true);
    expect(fake.signals).toEqual(["SIGTERM"]);
  });

  it("waits for child retirement before rejecting failed initialization", async () => {
    const fake = createFakeProcess();
    const started = AgyProcessController.start(
      {
        invocation: { cwd: "/workspace" },
        limits: LIMITS,
      },
      spawnReturning(fake),
    );
    let settled = false;
    const captured = started.catch((error: unknown) => {
      settled = true;
      return error;
    });

    fake.stdout.write("{broken}\n");
    await immediate();

    expect(fake.signals).toEqual(["SIGTERM"]);
    expect(settled).toBe(false);

    fake.emitExit(1);
    const error = await captured;
    expect(error).toMatchObject({ code: "INVALID_OUTPUT" });
  });
});

describe("AgyProcessController result barrier", () => {
  it("rejects the turn when a terminal result listener fails", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    controller.onEvent((event) => {
      if (event.kind === "result") {
        throw new Error("result listener failed");
      }
    });

    const turn = controller.runTurn({ event: "user" });
    fake.stdout.write(resultLine());

    await expect(turn).rejects.toMatchObject({
      code: "EVENT_HANDLER_FAILED",
    });
    expect(fake.signals).toEqual(["SIGTERM"]);
  });
});

describe("AgyProcessController exit-result arbitration", () => {
  it("rejects success when a non-zero exit is known before result acceptance", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    let listenerStarted = false;
    controller.onEvent(async (event) => {
      if (event.kind === "result") {
        listenerStarted = true;
        await gate;
      }
    });

    const turn = controller.runTurn({ event: "user" });
    fake.stdout.write(resultLine());
    await immediate();
    expect(listenerStarted).toBe(true);
    fake.emitExitOnly(7);
    release();

    await expect(turn).rejects.toMatchObject({ code: "PROCESS_EXITED" });
    fake.emitClose(7);
  });
});

describe("AgyProcessController final shutdown barriers", () => {
  it("hard-kills the process tree before resolving after direct-child close", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);
    let settled = false;

    const closing = controller.close().then(() => {
      settled = true;
    });
    fake.emitExit(0);
    await immediate();
    await immediate();

    expect(fake.signals).toContain("SIGKILL");
    expect(settled).toBe(false);

    await closing;
    expect(settled).toBe(true);
  });

  it("applies the final cleanup grace after hard termination", async () => {
    vi.useFakeTimers();
    const fake = createFakeProcess();
    const controller = await startReady(fake, {
      cancelGraceMs: 20,
      hardKillGraceMs: 30,
    });
    let settled = false;
    const closing = controller.close().then(() => {
      settled = true;
    });

    expect(fake.signals).toEqual(["SIGTERM"]);

    await vi.advanceTimersByTimeAsync(20);
    expect(fake.signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(29);
    expect(settled).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    await closing;
    expect(settled).toBe(true);
  });

  it("rejects a new turn after exit even before close", async () => {
    const fake = createFakeProcess();
    const controller = await startReady(fake);

    fake.emitExitOnly(0);

    expect(controller.isClosed).toBe(true);
    await expect(controller.runTurn({ event: "user" })).rejects.toMatchObject({
      code: "PROCESS_CLOSED",
    });
    fake.emitClose(0);
  });
});
