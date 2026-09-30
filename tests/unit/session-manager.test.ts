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
  maxSessions: 16,
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

interface Deferred<T> {
  readonly promise: Promise<T>;
  readonly resolve: (value: T | PromiseLike<T>) => void;
  readonly reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
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

  it("treats cancellation without a pending prompt as a no-op", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });
    const original = harness.controllers[0];

    await manager.cancel(sessionId);
    expect(original?.cancelCount).toBe(0);

    await manager.prompt(sessionId, { event: "after-idle-cancel" });
    expect(harness.invocations).toHaveLength(1);
    expect(original?.turns).toEqual([{ event: "after-idle-cancel" }]);
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

class DeferredController extends FakeController {
  public readonly turn = deferred<AgyResultEvent>();
  public readonly retirement = deferred<void>();
  #turnStarted = false;

  public override runTurn(value: unknown): Promise<AgyResultEvent> {
    this.turns.push(value);
    this.#turnStarted = true;
    return this.turn.promise;
  }

  public override cancel(): Promise<void> {
    this.cancelCount += 1;
    this.isClosed = true;
    if (this.#turnStarted) {
      this.turn.reject(new AgyProcessControllerError("CANCELLED"));
    }
    return this.retirement.promise;
  }
}

describe("SessionManager lifecycle races", () => {
  it("does not run a prompt cancelled before controller activation", async () => {
    const harness = controllerHarness();
    const manager = new SessionManager({ limits: LIMITS }, harness.factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });

    const prompting = manager.prompt(sessionId, { event: "cancelled" });
    await manager.cancel(sessionId);

    await expect(prompting).rejects.toMatchObject({ code: "CANCELLED" });
    expect(harness.controllers[0]?.turns).toEqual([]);
  });

  it("waits for a retiring process before lazy restart", async () => {
    const controllers: FakeController[] = [];
    const original = new DeferredController("session-1");
    const factory: AgyControllerFactory = (invocation) => {
      const controller =
        controllers.length === 0
          ? original
          : new FakeController(invocation.conversationId ?? "unexpected");
      controllers.push(controller);
      return Promise.resolve(controller);
    };
    const manager = new SessionManager({ limits: LIMITS }, factory);
    const sessionId = await manager.createSession({ cwd: "/workspace" });
    const running = manager.prompt(sessionId, { event: "running" });
    const runningAssertion = expect(running).rejects.toMatchObject({
      code: "CANCELLED",
    });
    await Promise.resolve();

    const cancelling = manager.cancel(sessionId);
    await Promise.resolve();
    const nextPrompt = manager.prompt(sessionId, { event: "next" });
    await Promise.resolve();

    expect(controllers).toHaveLength(1);
    original.retirement.resolve();
    await cancelling;
    await runningAssertion;
    await expect(nextPrompt).resolves.toMatchObject({
      conversationId: sessionId,
    });
    expect(controllers).toHaveLength(2);
  });

  it("makes closeAll an idempotent barrier for initializing sessions", async () => {
    const startup = deferred<ManagedAgyProcess>();
    const controller = new FakeController("late-session");
    const manager = new SessionManager({ limits: LIMITS }, () => startup.promise);
    const creating = manager.createSession({ cwd: "/workspace" });
    const creatingAssertion = expect(creating).rejects.toMatchObject({
      code: "MANAGER_CLOSED",
    });

    const firstClose = manager.closeAll();
    const secondClose = manager.closeAll();
    let settled = false;
    void firstClose.then(() => {
      settled = true;
    });

    expect(secondClose).toBe(firstClose);
    await Promise.resolve();
    expect(settled).toBe(false);

    startup.resolve(controller);
    await creatingAssertion;
    await firstClose;
    expect(controller.closeCount).toBe(1);
    expect(manager.size).toBe(0);
    await expect(manager.createSession({ cwd: "/late" })).rejects.toMatchObject({
      code: "MANAGER_CLOSED",
    });
  });

  it("reserves a session id before concurrent load startup", async () => {
    const firstStartup = deferred<ManagedAgyProcess>();
    const firstController = new FakeController("existing-id");
    const secondController = new FakeController("existing-id");
    let calls = 0;
    const manager = new SessionManager({ limits: LIMITS }, () => {
      calls += 1;
      return calls === 1
        ? firstStartup.promise
        : Promise.resolve(secondController);
    });

    const first = manager.loadSession({
      sessionId: "existing-id",
      cwd: "/workspace",
    });
    const second = manager.loadSession({
      sessionId: "existing-id",
      cwd: "/workspace",
    });
    firstStartup.resolve(firstController);

    await expect(first).resolves.toBe("existing-id");
    await expect(second).rejects.toMatchObject({ code: "SESSION_EXISTS" });
    expect(calls).toBe(1);
    expect(secondController.closeCount).toBe(0);
    await manager.closeAll();
  });
});

describe("SessionManager admission", () => {
  it("counts starting sessions and releases capacity after close", async () => {
    const starts: Array<Deferred<ManagedAgyProcess>> = [];
    const manager = new SessionManager(
      { limits: { ...LIMITS, maxSessions: 1 } },
      () => {
        const start = deferred<ManagedAgyProcess>();
        starts.push(start);
        return start.promise;
      },
    );

    const first = manager.createSession({ cwd: "/first" });
    await expect(manager.createSession({ cwd: "/second" })).rejects.toMatchObject({
      code: "SESSION_LIMIT",
    });
    expect(starts).toHaveLength(1);

    starts[0]?.resolve(new FakeController("session-1"));
    await expect(first).resolves.toBe("session-1");
    await manager.closeSession("session-1");

    const next = manager.createSession({ cwd: "/next" });
    expect(starts).toHaveLength(2);
    starts[1]?.resolve(new FakeController("session-2"));
    await expect(next).resolves.toBe("session-2");
    await manager.closeAll();
  });
});
