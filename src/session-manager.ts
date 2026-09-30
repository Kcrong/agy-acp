import type { AgyEvent, AgyResultEvent } from "./agy-events.js";
import type { AgyInvocationOptions } from "./agy-process.js";
import type { RuntimeLimits } from "./config.js";
import {
  AgyProcessController,
  type ManagedAgyProcess,
} from "./process-controller.js";

export type SessionManagerErrorCode =
  | "UNKNOWN_SESSION"
  | "SESSION_EXISTS"
  | "SESSION_ID_MISMATCH"
  | "INVALID_SESSION";

export interface SessionManagerOptions {
  readonly limits: RuntimeLimits;
  readonly executable?: string;
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
) => Promise<ManagedAgyProcess>;

interface SessionRecord {
  readonly sessionId: string;
  readonly cwd: string;
  readonly additionalDirectories: readonly string[];
  controller: ManagedAgyProcess | undefined;
  starting: Promise<ManagedAgyProcess> | undefined;
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

  public constructor(
    options: SessionManagerOptions,
    factory?: AgyControllerFactory,
  ) {
    this.#options = options;
    this.#factory =
      factory ??
      ((invocation) =>
        AgyProcessController.start({
          invocation,
          limits: options.limits,
        }));
  }

  public get size(): number {
    return this.#sessions.size;
  }

  public async createSession(options: CreateSessionOptions): Promise<string> {
    const controller = await this.#factory(this.#buildInvocation(options));
    const sessionId = controller.conversationId;

    if (this.#sessions.has(sessionId)) {
      await controller.close();
      throw new SessionManagerError("SESSION_EXISTS");
    }

    const record = this.#newRecord(sessionId, options, controller);
    this.#sessions.set(sessionId, record);
    this.#watchFailure(record, controller);
    return sessionId;
  }

  public async loadSession(options: LoadSessionOptions): Promise<string> {
    requireSessionId(options.sessionId);
    if (this.#sessions.has(options.sessionId)) {
      throw new SessionManagerError("SESSION_EXISTS");
    }

    const controller = await this.#factory(
      this.#buildInvocation(options, options.sessionId),
    );
    if (controller.conversationId !== options.sessionId) {
      await controller.close();
      throw new SessionManagerError("SESSION_ID_MISMATCH");
    }

    const record = this.#newRecord(options.sessionId, options, controller);
    this.#sessions.set(options.sessionId, record);
    this.#watchFailure(record, controller);
    return options.sessionId;
  }

  public async prompt(
    sessionId: string,
    input: unknown,
    onEvent?: (event: AgyEvent) => void,
  ): Promise<AgyResultEvent> {
    const record = this.#requireRecord(sessionId);
    const controller = await this.#ensureController(record);
    const unsubscribe =
      onEvent === undefined ? undefined : controller.onEvent(onEvent);

    try {
      return await controller.runTurn(input);
    } finally {
      unsubscribe?.();
      if (controller.isClosed && record.controller === controller) {
        record.controller = undefined;
      }
    }
  }

  public async cancel(sessionId: string): Promise<void> {
    const record = this.#requireRecord(sessionId);
    const controller = record.controller;
    if (controller === undefined) {
      return;
    }

    await controller.cancel();
    if (controller.isClosed && record.controller === controller) {
      record.controller = undefined;
    }
  }

  public async closeSession(sessionId: string): Promise<void> {
    const record = this.#requireRecord(sessionId);
    this.#sessions.delete(sessionId);
    await this.#closeRecord(record);
  }

  public async closeAll(): Promise<void> {
    const records = [...this.#sessions.values()];
    this.#sessions.clear();
    await Promise.all(records.map((record) => this.#closeRecord(record)));
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
    };
  }

  #requireRecord(sessionId: string): SessionRecord {
    requireSessionId(sessionId);
    const record = this.#sessions.get(sessionId);
    if (record === undefined) {
      throw new SessionManagerError("UNKNOWN_SESSION");
    }
    return record;
  }

  async #ensureController(record: SessionRecord): Promise<ManagedAgyProcess> {
    if (record.controller !== undefined && !record.controller.isClosed) {
      return record.controller;
    }
    if (record.starting !== undefined) {
      return record.starting;
    }

    const starting = this.#factory(
      this.#buildInvocation(record, record.sessionId),
    );
    record.starting = starting;

    try {
      const controller = await starting;
      if (controller.conversationId !== record.sessionId) {
        await controller.close();
        throw new SessionManagerError("SESSION_ID_MISMATCH");
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
        record.controller = undefined;
      }
    });
  }

  async #closeRecord(record: SessionRecord): Promise<void> {
    const starting = record.starting;
    const controller =
      record.controller ?? (starting === undefined ? undefined : await starting);
    record.controller = undefined;
    record.starting = undefined;
    await controller?.close();
  }

  #buildInvocation(
    options: CreateSessionOptions,
    conversationId?: string,
  ): AgyInvocationOptions {
    const invocation: {
      cwd: string;
      executable?: string;
      conversationId?: string;
      additionalDirectories?: readonly string[];
      env?: NodeJS.ProcessEnv;
    } = { cwd: options.cwd };

    if (this.#options.executable !== undefined) {
      invocation.executable = this.#options.executable;
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
  }
}
