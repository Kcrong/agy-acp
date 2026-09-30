import { describe, expect, it } from "vitest";

import type { AgyEvent, AgyResultEvent } from "../../src/agy-events.js";
import type { AgyInvocationOptions } from "../../src/agy-process.js";
import type { RuntimeLimits } from "../../src/config.js";
import {
  AgyProcessControllerError,
  type ManagedAgyProcess,
} from "../../src/process-controller.js";
import {
  SessionManager,
  SessionManagerError,
} from "../../src/session-manager.js";
import type { AgyControllerFactory } from "../../src/session-manager.js";

const LIMITS: RuntimeLimits = {
  maxLineBytes: 1_024,
  maxStderrBytes: 1_024,
  initTimeoutMs: 100,
  promptTimeoutMs: 1_000,
  cancelGraceMs: 50,
  hardKillGraceMs: 50,
};

function result(conversationId: string): AgyResultEvent {
  return {
    kind: "result",
    conversationId,
    durationSeconds: 1,
    error: null,
    numTurns: 1,
    response: `response:${conversationId}`,
    status: "SUCCESS",
    usage: {},
  };
}

class FakeController implements ManagedAgyProcess {
  public isClosed = false;
  public readonly turns: unknown[] = [];
  public cancelCount = 0;
  public closeCount = 0;
  readonly #eventListeners = new Set<(event: AgyEvent) => void>();
  readonly #failureListeners = new Set<
    (error: AgyProcessControllerError) => void
  >();

  public constructor(public readonly conversationId: string) {}

  public runTurn(value: unknown): Promise<AgyResultEvent> {
    this.turns.push(value);
    return Promise.resolve(result(this.conversationId));
  }

  public cancel(): Promise<void> {
    this.cancelCount += 1;
    this.isClosed = true;
    return Promise.resolve();
  }

  public close(): Promise<void> {
    this.closeCount += 1;
    this.isClosed = true;
    return Promise.resolve();
  }

  public onEvent(listener: (event: AgyEvent) => void): () => void {
    this.#eventListeners.add(listener);
    return () => this.#eventListeners.delete(listener);
  }

  public onFailure(
    listener: (error: AgyProcessControllerError) => void,
  ): () => void {
    this.#failureListeners.add(listener);
    return () => this.#failureListeners.delete(listener);
  }

  public emitFailure(error: AgyProcessControllerError): void {
    this.isClosed = true;
    for (const listener of this.#failureListeners) {
      listener(error);
    }
  }
}

function controllerHarness(options?: { readonly fixedId?: string }) {
  const invocations: AgyInvocationOptions[] = [];
  const controllers: FakeController[] = [];
  let sequence = 0;
  const factory: AgyControllerFactory = (invocation) => {
    invocations.push(invocation);
    const id =
      options?.fixedId ??
      invocation.conversationId ??
      `session-${String(++sequence)}`;
    const controller = new FakeController(id);
    controllers.push(controller);
    return Promise.resolve(controller);
  };

  return { factory, invocations, controllers };
}

describe("SessionManager", () => {
  it("isolates controllers and prompts for multiple sessions", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager(
      { limits: LIMITS, executable: "/custom/agy" },
      harness.factory,
    );

    const first = await manager.createSession({
      cwd: "/first",
      additionalDirectories: ["/shared"],
    });
    const second = await manager.createSession({ cwd: "/second" });

    expect(first).toBe("session-1");
    expect(second).toBe("session-2");
    expect(manager.size).toBe(2);
    await expect(manager.prompt(first, { event: "first" })).resolves.toMatchObject({
      conversationId: "session-1",
    });
    await expect(manager.prompt(second, { event: "second" })).resolves.toMatchObject({
      conversationId: "session-2",
    });
    expect(harness.invocations).toEqual([
      {
        cwd: "/first",
        additionalDirectories: ["/shared"],
        executable: "/custom/agy",
      },
      { cwd: "/second", executable: "/custom/agy" },
    ]);
  });

  it("rejects a duplicate new-session id and closes the duplicate controller", async () => {
    const harness = controllerHarness({ fixedId: "same-id" });
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);

    await manager.createSession({ cwd: "/first" });
    await expect(manager.createSession({ cwd: "/second" })).rejects.toMatchObject({
      code: "SESSION_EXISTS",
    });
    expect(harness.controllers[1]?.closeCount).toBe(1);
    expect(manager.size).toBe(1);
  });

  it("loads by opaque id and rejects a mismatched controller id", async () => {
    const good = controllerHarness();
    const goodManager = new SessionManager({ limits: LIMITS }, good.factory);

    await expect(
      goodManager.loadSession({ sessionId: "existing-id", cwd: "/workspace" }),
    ).resolves.toBe("existing-id");
    expect(good.invocations[0]).toMatchObject({
      conversationId: "existing-id",
      cwd: "/workspace",
    });

    const mismatched = controllerHarness({ fixedId: "different-id" });
    const badManager = new SessionManager({ limits: LIMITS }, mismatched.factory);
    await expect(
      badManager.loadSession({ sessionId: "expected-id", cwd: "/workspace" }),
    ).rejects.toMatchObject({ code: "SESSION_ID_MISMATCH" });
    expect(mismatched.controllers[0]?.closeCount).toBe(1);
  });

  it("restarts lazily with the same opaque id after cancellation", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });
    const original = harness.controllers[0];

    await manager.cancel(sessionId);
    expect(original?.cancelCount).toBe(1);

    await manager.prompt(sessionId, { event: "after-cancel" });
    expect(harness.invocations[1]).toMatchObject({
      conversationId: sessionId,
      cwd: "/workspace",
    });
    expect(harness.controllers[1]?.turns).toEqual([
      { event: "after-cancel" },
    ]);
  });

  it("drops a failed controller and restarts it on the next prompt", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });

    harness.controllers[0]?.emitFailure(
      new AgyProcessControllerError("PROCESS_EXITED"),
    );

    await manager.prompt(sessionId, { event: "retry" });
    expect(harness.invocations[1]?.conversationId).toBe(sessionId);
  });

  it("closes one session and rejects later operations without echoing its id", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });

    await manager.closeSession(sessionId);
    expect(manager.size).toBe(0);

    let thrown: unknown;
    try {
      await manager.prompt(sessionId, { event: "late" });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(SessionManagerError);
    if (!(thrown instanceof Error)) {
      throw new TypeError("expected SessionManagerError");
    }
    expect(thrown.message).not.toContain(sessionId);
  });

  it("closes every controller even when sessions are independent", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    await manager.createSession({ cwd: "/first" });
    await manager.createSession({ cwd: "/second" });

    await manager.closeAll();

    expect(manager.size).toBe(0);
    expect(harness.controllers.map((controller) => controller.closeCount)).toEqual([
      1, 1,
    ]);
  });
});
