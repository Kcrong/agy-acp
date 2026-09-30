import type { AgyResultEvent } from "./agy-events.js";
import type { AgyInvocationOptions } from "./agy-process.js";
import type { RuntimeLimits } from "./config.js";
import {
  AgyProcessController,
  AgyProcessControllerError,
  type AgyEventListener,
  type ManagedAgyProcess,
} from "./process-controller.js";

export type SessionManagerErrorCode =
  | "UNKNOWN_SESSION"
  | "SESSION_EXISTS"
  | "SESSION_ID_MISMATCH"
  | "INVALID_SESSION"
  | "MANAGER_CLOSED"
  | "SESSION_BUSY"
  | "SESSION_LIMIT";

export interface SessionManagerOptions {
  readonly limits: RuntimeLimits;
  readonly executable?: string;
  readonly executableArguments?: readonly string[];
  readonly env?: NodeJS.ProcessEnv;
}

export interface CreateSessionOptions {
  readonly cwd: string;
  readonly additionalDirectories?: readonly string[];
}

export interface LoadSessionOptions extends CreateSessionOptions {
  readonly sessionId: string;
}

export type AgyControllerFactory = (
  invocation: AgyInvocationOptions,
  signal?: AbortSignal,
) => Promise<ManagedAgyProcess>;

interface PendingPrompt {
  readonly abort: AbortController;
  cancelled: boolean;
  active: boolean;
  controller: ManagedAgyProcess | undefined;
}

interface SessionRecord {
  readonly sessionId: string;
  readonly cwd: string;
  readonly additionalDirectories: readonly string[];
  controller: ManagedAgyProcess | undefined;
  starting: Promise<ManagedAgyProcess> | undefined;
  retiring: Promise<void> | undefined;
  prompt: PendingPrompt | undefined;
  closed: boolean;
}

export class SessionManagerError extends Error {
  public readonly code: SessionManagerErrorCode;

  public constructor(code: SessionManagerErrorCode) {
    super(sessionErrorMessage(code));
    this.name = "SessionManagerError";
    this.code = code;
  }
}

export class SessionManager {
  readonly #options: SessionManagerOptions;
  readonly #factory: AgyControllerFactory;
  readonly #sessions = new Map<string, SessionRecord>();
  readonly #pendingStarts = new Set<Promise<void>>();
  readonly #reservedSessionIds = new Set<string>();
  #startingSessions = 0;
  #closed = false;
  #closePromise: Promise<void> | undefined;

  public constructor(
    options: SessionManagerOptions,
    factory?: AgyControllerFactory,
  ) {
    this.#options = options;
    this.#factory =
      factory ??
      ((invocation, signal) =>
        AgyProcessController.start({
          invocation,
          limits: options.limits,
          ...(signal === undefined ? {} : { signal }),
        }));
  }

  public get size(): number {
    return this.#sessions.size;
  }

  public async createSession(
    options: CreateSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.#assertOpen();
    if (signal?.aborted === true) {
      throw new AgyProcessControllerError("CANCELLED");
    }
    this.#reserveSessionSlot();
    return this.#trackStart(async () => {
      const controller = await this.#factory(
        this.#buildInvocation(options),
        signal,
      );
      if (signal?.aborted === true) {
        await controller.close();
        throw new AgyProcessControllerError("CANCELLED");
      }
      if (this.#closed) {
        await controller.close();
        throw new SessionManagerError("MANAGER_CLOSED");
      }

      const sessionId = controller.conversationId;
      if (
        this.#sessions.has(sessionId) ||
        this.#reservedSessionIds.has(sessionId)
      ) {
        await controller.close();
        throw new SessionManagerError("SESSION_EXISTS");
      }

      const record = this.#newRecord(sessionId, options, controller);
      this.#sessions.set(sessionId, record);
      this.#watchFailure(record, controller);
      return sessionId;
    }).finally(() => {
      this.#startingSessions -= 1;
    });
  }

  public async loadSession(
    options: LoadSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.#assertOpen();
    if (signal?.aborted === true) {
      throw new AgyProcessControllerError("CANCELLED");
    }
    requireSessionId(options.sessionId);
    if (
      this.#sessions.has(options.sessionId) ||
      this.#reservedSessionIds.has(options.sessionId)
    ) {
      return Promise.reject(new SessionManagerError("SESSION_EXISTS"));
    }

    this.#reserveSessionSlot();
    this.#reservedSessionIds.add(options.sessionId);
    return this.#trackStart(async () => {
      try {
        const controller = await this.#factory(
          this.#buildInvocation(options, options.sessionId),
          signal,
        );
        if (signal?.aborted === true) {
          await controller.close();
          throw new AgyProcessControllerError("CANCELLED");
        }
        if (this.#closed) {
          await controller.close();
          throw new SessionManagerError("MANAGER_CLOSED");
        }
        if (controller.conversationId !== options.sessionId) {
          await controller.close();
          throw new SessionManagerError("SESSION_ID_MISMATCH");
        }
        if (this.#sessions.has(options.sessionId)) {
          await controller.close();
          throw new SessionManagerError("SESSION_EXISTS");
        }

        const record = this.#newRecord(options.sessionId, options, controller);
        this.#sessions.set(options.sessionId, record);
        this.#watchFailure(record, controller);
        return options.sessionId;
      } finally {
        this.#reservedSessionIds.delete(options.sessionId);
      }
    }).finally(() => {
      this.#startingSessions -= 1;
    });
  }

  public prompt(
    sessionId: string,
    input: unknown,
    onEvent?: AgyEventListener,
  ): Promise<AgyResultEvent> {
    const record = this.#requireRecord(sessionId);
    if (record.prompt !== undefined) {
      return Promise.reject(new SessionManagerError("SESSION_BUSY"));
    }

    const pending: PendingPrompt = {
      abort: new AbortController(),
      cancelled: false,
      active: false,
      controller: undefined,
    };
    record.prompt = pending;
    return this.#runPrompt(record, pending, input, onEvent);
  }

  async #runPrompt(
    record: SessionRecord,
    pending: PendingPrompt,
    input: unknown,
    onEvent?: AgyEventListener,
  ): Promise<AgyResultEvent> {
    let controller: ManagedAgyProcess | undefined;
    let unsubscribe: (() => void) | undefined;

    try {
      controller = await this.#ensureController(record, pending.abort.signal);
      if (pending.cancelled || record.closed || this.#closed) {
        throw new AgyProcessControllerError("CANCELLED");
      }

      pending.controller = controller;
      pending.active = true;
      unsubscribe =
        onEvent === undefined ? undefined : controller.onEvent(onEvent);
      return await controller.runTurn(input);
    } finally {
      unsubscribe?.();
      pending.active = false;
      if (record.prompt === pending) {
        record.prompt = undefined;
      }
      if (controller?.isClosed === true) {
        void this.#retireController(
          record,
          controller,
          controller.close(),
        ).catch(() => undefined);
      }
    }
  }

  public cancel(sessionId: string): Promise<void> {
    const record = this.#requireRecord(sessionId);
    const pending = record.prompt;
    if (pending === undefined) {
      return Promise.resolve();
    }

    pending.cancelled = true;
    pending.abort.abort();
    const controller = pending.controller;
    if (!pending.active || controller === undefined) {
      return Promise.resolve();
    }

    return this.#retireController(record, controller, controller.cancel());
  }

  public async closeSession(sessionId: string): Promise<void> {
    const record = this.#requireRecord(sessionId);
    record.closed = true;
    try {
      const pending = record.prompt;
      if (pending !== undefined) {
        pending.cancelled = true;
        pending.abort.abort();
        if (pending.active && pending.controller !== undefined) {
          await this.#retireController(
            record,
            pending.controller,
            pending.controller.cancel(),
          );
        }
      }
      await this.#closeRecord(record);
    } finally {
      if (this.#sessions.get(sessionId) === record) {
        this.#sessions.delete(sessionId);
      }
    }
  }

  public closeAll(): Promise<void> {
    if (this.#closePromise !== undefined) {
      return this.#closePromise;
    }

    this.#closed = true;
    const records = [...this.#sessions.values()];
    this.#sessions.clear();
    for (const record of records) {
      record.closed = true;
      if (record.prompt !== undefined) {
        record.prompt.cancelled = true;
        record.prompt.abort.abort();
      }
    }

    this.#closePromise = (async () => {
      await Promise.all(records.map((record) => this.#closeRecord(record)));
      while (this.#pendingStarts.size > 0) {
        await Promise.all([...this.#pendingStarts]);
      }
    })();
    return this.#closePromise;
  }

  #newRecord(
    sessionId: string,
    options: CreateSessionOptions,
    controller: ManagedAgyProcess,
  ): SessionRecord {
    return {
      sessionId,
      cwd: options.cwd,
      additionalDirectories: [...(options.additionalDirectories ?? [])],
      controller,
      starting: undefined,
      retiring: undefined,
      prompt: undefined,
      closed: false,
    };
  }

  #requireRecord(sessionId: string): SessionRecord {
    this.#assertOpen();
    requireSessionId(sessionId);
    const record = this.#sessions.get(sessionId);
    if (record === undefined || record.closed) {
      throw new SessionManagerError("UNKNOWN_SESSION");
    }
    return record;
  }

  async #ensureController(
    record: SessionRecord,
    signal?: AbortSignal,
  ): Promise<ManagedAgyProcess> {
    throwIfCancelled(signal);
    if (record.retiring !== undefined) {
      await record.retiring;
      throwIfCancelled(signal);
    }
    if (record.closed || this.#closed) {
      throw new SessionManagerError("MANAGER_CLOSED");
    }
    if (record.controller !== undefined && !record.controller.isClosed) {
      return record.controller;
    }
    if (record.controller?.isClosed === true) {
      await this.#retireController(
        record,
        record.controller,
        record.controller.close(),
      );
      throwIfCancelled(signal);
    }
    if (record.starting !== undefined) {
      const controller = await record.starting;
      throwIfCancelled(signal);
      return controller;
    }

    const starting = this.#trackStart(async () => {
      const controller = await this.#factory(
        this.#buildInvocation(record, record.sessionId),
        signal,
      );
      if (signal?.aborted === true) {
        await controller.close();
        throw new AgyProcessControllerError("CANCELLED");
      }
      if (record.closed || this.#closed) {
        await controller.close();
        throw new SessionManagerError("MANAGER_CLOSED");
      }
      if (controller.conversationId !== record.sessionId) {
        await controller.close();
        throw new SessionManagerError("SESSION_ID_MISMATCH");
      }
      return controller;
    });
    record.starting = starting;

    try {
      const controller = await starting;
      if (signal?.aborted === true) {
        await controller.close();
        throw new AgyProcessControllerError("CANCELLED");
      }
      record.controller = controller;
      this.#watchFailure(record, controller);
      return controller;
    } finally {
      record.starting = undefined;
    }
  }

  #watchFailure(
    record: SessionRecord,
    controller: ManagedAgyProcess,
  ): void {
    controller.onFailure(() => {
      if (record.controller === controller) {
        void this.#retireController(
          record,
          controller,
          controller.close(),
        ).catch(() => undefined);
      }
    });
  }

  #retireController(
    record: SessionRecord,
    controller: ManagedAgyProcess,
    retirement: Promise<void>,
  ): Promise<void> {
    if (record.retiring !== undefined) {
      return record.retiring;
    }

    const retiring = retirement.finally(() => {
      if (record.controller === controller) {
        record.controller = undefined;
      }
      if (record.retiring === retiring) {
        record.retiring = undefined;
      }
    });
    record.retiring = retiring;
    return retiring;
  }

  async #closeRecord(record: SessionRecord): Promise<void> {
    const controllers = new Set<ManagedAgyProcess>();
    if (record.controller !== undefined) {
      controllers.add(record.controller);
    }
    if (record.starting !== undefined) {
      try {
        controllers.add(await record.starting);
      } catch {
        // A startup rejected or closed itself after the manager closed.
      }
    }
    if (record.retiring !== undefined) {
      await record.retiring;
    }

    record.controller = undefined;
    record.starting = undefined;
    record.retiring = undefined;
    await Promise.all([...controllers].map((controller) => controller.close()));
  }

  #trackStart<T>(operation: () => Promise<T>): Promise<T> {
    const start = operation();
    const barrier = start.then(
      () => undefined,
      () => undefined,
    );
    this.#pendingStarts.add(barrier);
    void barrier.finally(() => this.#pendingStarts.delete(barrier));
    return start;
  }

  #reserveSessionSlot(): void {
    if (
      this.#sessions.size + this.#startingSessions >=
      this.#options.limits.maxSessions
    ) {
      throw new SessionManagerError("SESSION_LIMIT");
    }
    this.#startingSessions += 1;
  }

  #assertOpen(): void {
    if (this.#closed) {
      throw new SessionManagerError("MANAGER_CLOSED");
    }
  }

  #buildInvocation(
    options: CreateSessionOptions,
    conversationId?: string,
  ): AgyInvocationOptions {
    const invocation: {
      cwd: string;
      executable?: string;
      executableArguments?: readonly string[];
      conversationId?: string;
      additionalDirectories?: readonly string[];
      env?: NodeJS.ProcessEnv;
    } = { cwd: options.cwd };

    if (this.#options.executable !== undefined) {
      invocation.executable = this.#options.executable;
    }
    if (this.#options.executableArguments !== undefined) {
      invocation.executableArguments = [...this.#options.executableArguments];
    }
    if (this.#options.env !== undefined) {
      invocation.env = this.#options.env;
    }
    if (options.additionalDirectories !== undefined) {
      invocation.additionalDirectories = [...options.additionalDirectories];
    }
    if (conversationId !== undefined) {
      invocation.conversationId = conversationId;
    }

    return invocation;
  }
}

function throwIfCancelled(signal?: AbortSignal): void {
  if (signal?.aborted === true) {
    throw new AgyProcessControllerError("CANCELLED");
  }
}

function requireSessionId(sessionId: string): void {
  if (sessionId.length === 0 || sessionId.includes("\0")) {
    throw new SessionManagerError("INVALID_SESSION");
  }
}

function sessionErrorMessage(code: SessionManagerErrorCode): string {
  switch (code) {
    case "UNKNOWN_SESSION":
      return "Unknown agy session";
    case "SESSION_EXISTS":
      return "agy session already exists";
    case "SESSION_ID_MISMATCH":
      return "agy session identity mismatch";
    case "INVALID_SESSION":
      return "Invalid agy session identifier";
    case "MANAGER_CLOSED":
      return "agy session manager is closed";
    case "SESSION_BUSY":
      return "agy session already has a pending prompt";
    case "SESSION_LIMIT":
      return "agy session limit reached";
  }
}
