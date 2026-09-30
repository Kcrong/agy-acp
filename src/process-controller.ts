import type { ChildProcessWithoutNullStreams } from "node:child_process";
import type { Writable } from "node:stream";

import {
  parseAgyEvent,
  type AgyEvent,
  type AgyResultEvent,
} from "./agy-events.js";
import {
  AgyProcessConfigError,
  signalAgyProcessTree,
  spawnAgyProcess,
  type AgyInvocationOptions,
  type AgySpawnFunction,
} from "./agy-process.js";
import type { RuntimeLimits } from "./config.js";
import { NdjsonParser } from "./ndjson.js";

export type AgyProcessControllerErrorCode =
  | "SPAWN_FAILED"
  | "INIT_TIMEOUT"
  | "PROCESS_EXITED"
  | "INVALID_OUTPUT"
  | "WRITE_FAILED"
  | "PROCESS_CLOSED"
  | "PROCESS_BUSY"
  | "PROMPT_TIMEOUT"
  | "CANCELLED"
  | "EVENT_HANDLER_FAILED";

interface ActiveTurn {
  readonly resolve: (result: AgyResultEvent) => void;
  readonly reject: (error: AgyProcessControllerError) => void;
  readonly timer: NodeJS.Timeout;
}

export interface AgyProcessControllerOptions {
  readonly invocation: AgyInvocationOptions;
  readonly limits: RuntimeLimits;
  readonly signal?: AbortSignal;
}

export interface AgyProcessDiagnostics {
  readonly stderrBytes: number;
  readonly stderrTruncated: boolean;
}

export type AgyEventListener = (event: AgyEvent) => void | Promise<void>;

export interface ManagedAgyProcess {
  readonly conversationId: string;
  readonly isClosed: boolean;
  runTurn(value: unknown): Promise<AgyResultEvent>;
  cancel(): Promise<void>;
  close(): Promise<void>;
  onEvent(listener: AgyEventListener): () => void;
  onFailure(listener: (error: AgyProcessControllerError) => void): () => void;
}

export class AgyProcessControllerError extends Error {
  public readonly code: AgyProcessControllerErrorCode;

  public constructor(code: AgyProcessControllerErrorCode, cause?: unknown) {
    super(errorMessage(code), cause === undefined ? undefined : { cause });
    this.name = "AgyProcessControllerError";
    this.code = code;
  }
}

export class AgyProcessController implements ManagedAgyProcess {
  readonly #child: ChildProcessWithoutNullStreams;
  readonly #limits: RuntimeLimits;
  readonly #startupSignal: AbortSignal | undefined;
  readonly #parser: NdjsonParser<unknown>;
  readonly #eventListeners = new Set<AgyEventListener>();
  readonly #failureListeners = new Set<
    (error: AgyProcessControllerError) => void
  >();
  readonly #ready: Promise<void>;
  readonly #resolveReady: () => void;
  readonly #rejectReady: (error: AgyProcessControllerError) => void;
  #eventQueue: Promise<void> = Promise.resolve();
  #stdoutEnded = false;
  #processExited = false;
  #processExitCode: number | null | undefined;
  #processExitSignal: NodeJS.Signals | null | undefined;
  #processClosed = false;
  #initTimer: NodeJS.Timeout | undefined;
  #activeTurn: ActiveTurn | undefined;
  #shutdownPromise: Promise<void> | undefined;
  #resolveShutdown: (() => void) | undefined;
  #signalBarrier: Promise<void> = Promise.resolve();
  #hardKillPromise: Promise<void> | undefined;
  #terminationTimer: NodeJS.Timeout | undefined;
  #hardKillTimer: NodeJS.Timeout | undefined;
  #conversationId: string | undefined;
  #stderr = Buffer.alloc(0);
  #stderrTruncated = false;
  #readySettled = false;
  #failed = false;
  #closed = false;

  private constructor(
    child: ChildProcessWithoutNullStreams,
    limits: RuntimeLimits,
    signal?: AbortSignal,
  ) {
    this.#child = child;
    this.#limits = limits;
    this.#startupSignal = signal;
    this.#parser = new NdjsonParser({ maxLineBytes: limits.maxLineBytes });

    let resolveReady: (() => void) | undefined;
    let rejectReady:
      | ((error: AgyProcessControllerError) => void)
      | undefined;
    this.#ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    this.#resolveReady = resolveReady as () => void;
    this.#rejectReady = rejectReady as (
      error: AgyProcessControllerError,
    ) => void;

    child.stdout.on("data", this.#handleStdout);
    child.stdout.on("end", this.#handleStdoutEnd);
    child.stdin.on("error", this.#handleStdinError);
    child.stderr.on("data", this.#handleStderr);
    child.on("error", this.#handleProcessError);
    child.on("exit", this.#handleExit);
    child.on("close", this.#handleClose);

    this.#initTimer = setTimeout(() => {
      this.#fail(new AgyProcessControllerError("INIT_TIMEOUT"));
    }, limits.initTimeoutMs);
    this.#initTimer.unref();

    signal?.addEventListener("abort", this.#handleStartupAbort, { once: true });
    if (signal?.aborted === true) {
      queueMicrotask(this.#handleStartupAbort);
    }
  }

  public static async start(
    options: AgyProcessControllerOptions,
    spawn?: AgySpawnFunction,
  ): Promise<AgyProcessController> {
    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawnAgyProcess(options.invocation, spawn);
    } catch (error) {
      if (error instanceof AgyProcessConfigError) {
        throw error;
      }
      throw new AgyProcessControllerError("SPAWN_FAILED", error);
    }

    const controller = new AgyProcessController(
      child,
      options.limits,
      options.signal,
    );
    try {
      await controller.#ready;
      return controller;
    } catch (error) {
      await controller.close();
      throw error;
    }
  }

  public get conversationId(): string {
    if (this.#conversationId === undefined) {
      throw new AgyProcessControllerError("PROCESS_CLOSED");
    }
    return this.#conversationId;
  }

  public get isClosed(): boolean {
    return this.#closed || this.#processExited;
  }

  public get diagnostics(): AgyProcessDiagnostics {
    return {
      stderrBytes: this.#stderr.byteLength,
      stderrTruncated: this.#stderrTruncated,
    };
  }

  public onEvent(listener: AgyEventListener): () => void {
    this.#eventListeners.add(listener);
    return () => this.#eventListeners.delete(listener);
  }

  public onFailure(
    listener: (error: AgyProcessControllerError) => void,
  ): () => void {
    this.#failureListeners.add(listener);
    return () => this.#failureListeners.delete(listener);
  }

  public async send(value: unknown): Promise<void> {
    if (
      this.#closed ||
      this.#failed ||
      this.#processExited ||
      this.#conversationId === undefined
    ) {
      throw new AgyProcessControllerError("PROCESS_CLOSED");
    }

    let line: string;
    try {
      line = `${JSON.stringify(value)}\n`;
    } catch (error) {
      throw new AgyProcessControllerError("WRITE_FAILED", error);
    }

    if (Buffer.byteLength(line) > this.#limits.maxLineBytes) {
      throw new AgyProcessControllerError("WRITE_FAILED");
    }

    try {
      await writeWithBackpressure(this.#child.stdin, line);
    } catch (error) {
      if (error instanceof AgyProcessControllerError) {
        throw error;
      }
      throw new AgyProcessControllerError("WRITE_FAILED", error);
    }
  }

  public runTurn(value: unknown): Promise<AgyResultEvent> {
    if (this.#activeTurn !== undefined) {
      return Promise.reject(new AgyProcessControllerError("PROCESS_BUSY"));
    }
    if (
      this.#closed ||
      this.#failed ||
      this.#processExited ||
      this.#conversationId === undefined
    ) {
      return Promise.reject(new AgyProcessControllerError("PROCESS_CLOSED"));
    }

    const turn = new Promise<AgyResultEvent>((resolve, reject) => {
      const timer = setTimeout(() => {
        const error = new AgyProcessControllerError("PROMPT_TIMEOUT");
        this.#rejectActiveTurn(error);
        void this.#beginShutdown();
      }, this.#limits.promptTimeoutMs);
      timer.unref();
      this.#activeTurn = { resolve, reject, timer };
    });

    void this.send(value).catch((error: unknown) => {
      const wrapped =
        error instanceof AgyProcessControllerError
          ? error
          : new AgyProcessControllerError("WRITE_FAILED", error);
      this.#fail(wrapped);
    });

    return turn;
  }

  public cancel(): Promise<void> {
    if (this.#activeTurn === undefined) {
      return Promise.resolve();
    }

    this.#rejectActiveTurn(new AgyProcessControllerError("CANCELLED"));
    return this.#beginShutdown();
  }

  public close(): Promise<void> {
    if (this.#shutdownPromise !== undefined) {
      return this.#shutdownPromise;
    }
    if (this.#closed) {
      this.#cleanupListeners();
      return Promise.resolve();
    }

    this.#rejectActiveTurn(new AgyProcessControllerError("PROCESS_CLOSED"));
    return this.#beginShutdown();
  }

  readonly #handleStartupAbort = (): void => {
    if (!this.#readySettled) {
      this.#fail(new AgyProcessControllerError("CANCELLED"));
    }
  };

  readonly #handleStdout = (chunk: string | Uint8Array): void => {
    if (this.#failed || this.#closed) {
      return;
    }

    try {
      const events = this.#parser.push(chunk).map(parseAgyEvent);
      this.#queueEvents(events);
    } catch (error) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT", error));
    }
  };

  readonly #handleStdoutEnd = (): void => {
    if (this.#failed || this.#closed) {
      return;
    }

    this.#stdoutEnded = true;
    try {
      const events = this.#parser.finish().map(parseAgyEvent);
      this.#queueEvents(events);
      const pendingEvents = this.#eventQueue;
      void pendingEvents.then(() => {
        setImmediate(() => {
          if (!this.#processClosed && !this.#closed && !this.#failed) {
            const code = this.#processExited
              ? "PROCESS_EXITED"
              : "INVALID_OUTPUT";
            this.#fail(
              new AgyProcessControllerError(code),
              !this.#processExited,
            );
          }
        });
      });
    } catch (error) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT", error));
    }
  };

  readonly #handleStdinError = (error: Error): void => {
    this.#fail(new AgyProcessControllerError("WRITE_FAILED", error));
  };

  readonly #handleStderr = (chunk: string | Uint8Array): void => {
    const bytes =
      typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
    const maximum = this.#limits.maxStderrBytes;

    if (bytes.byteLength >= maximum) {
      this.#stderr = bytes.subarray(bytes.byteLength - maximum);
      this.#stderrTruncated = true;
      return;
    }

    const combined = Buffer.concat([this.#stderr, bytes]);
    if (combined.byteLength > maximum) {
      this.#stderr = combined.subarray(combined.byteLength - maximum);
      this.#stderrTruncated = true;
    } else {
      this.#stderr = combined;
    }
  };

  readonly #handleProcessError = (error: Error): void => {
    this.#fail(new AgyProcessControllerError("SPAWN_FAILED", error), false);
  };

  readonly #handleExit = (
    code: number | null,
    signal: NodeJS.Signals | null,
  ): void => {
    this.#processExited = true;
    this.#processExitCode = code;
    this.#processExitSignal = signal;
  };

  readonly #handleClose = (): void => {
    this.#processClosed = true;
    this.#closed = true;
    const pendingEvents = this.#eventQueue;
    void pendingEvents.then(() => {
      if (this.#shutdownPromise !== undefined) {
        void this.#startHardKill();
        return;
      }

      if (!this.#failed) {
        this.#fail(new AgyProcessControllerError("PROCESS_EXITED"), false);
      }
      this.#cleanupListeners();
    });
  };

  #queueEvents(events: readonly AgyEvent[]): void {
    if (events.length === 0) {
      return;
    }

    this.#child.stdout.pause();
    const queued = this.#eventQueue.then(async () => {
      for (const event of events) {
        await this.#acceptEvent(event);
      }
    });
    const handled = queued.catch((error: unknown) => {
      const wrapped =
        error instanceof AgyProcessControllerError
          ? error
          : new AgyProcessControllerError("EVENT_HANDLER_FAILED", error);
      this.#fail(wrapped);
    });
    this.#eventQueue = handled;
    void handled.then(() => {
      if (
        this.#eventQueue === handled &&
        !this.#failed &&
        !this.#closed &&
        !this.#stdoutEnded
      ) {
        this.#child.stdout.resume();
      }
    });
  }

  async #acceptEvent(event: AgyEvent): Promise<void> {
    if (!this.#readySettled) {
      if (event.kind !== "init") {
        if (event.kind === "unknown") {
          return;
        }
        this.#fail(new AgyProcessControllerError("INVALID_OUTPUT"));
        return;
      }

      this.#conversationId = event.conversationId;
      this.#readySettled = true;
      this.#startupSignal?.removeEventListener(
        "abort",
        this.#handleStartupAbort,
      );
      this.#clearInitTimer();
      this.#resolveReady();
      return;
    }

    if (
      event.kind !== "unknown" &&
      event.conversationId !== this.#conversationId
    ) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT"));
      return;
    }

    if (event.kind === "init") {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT"));
      return;
    }
    if (
      (event.kind === "step_update" || event.kind === "result") &&
      this.#activeTurn === undefined
    ) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT"));
      return;
    }

    const completingTurn =
      event.kind === "result" ? this.#activeTurn : undefined;

    for (const listener of this.#eventListeners) {
      try {
        await listener(event);
      } catch (error) {
        throw new AgyProcessControllerError("EVENT_HANDLER_FAILED", error);
      }
    }

    if (
      event.kind === "result" &&
      this.#processExited &&
      (this.#processExitCode !== 0 || this.#processExitSignal !== null)
    ) {
      this.#fail(new AgyProcessControllerError("PROCESS_EXITED"), false);
      return;
    }

    if (
      event.kind === "result" &&
      completingTurn !== undefined &&
      this.#activeTurn === completingTurn
    ) {
      this.#activeTurn = undefined;
      clearTimeout(completingTurn.timer);
      completingTurn.resolve(event);
    }
  }

  #fail(error: AgyProcessControllerError, terminate = true): void {
    if (this.#failed) {
      return;
    }

    this.#failed = true;
    this.#clearInitTimer();
    this.#rejectActiveTurn(error);
    if (terminate && !this.#closed) {
      void this.#beginShutdown();
    }

    if (!this.#readySettled) {
      this.#readySettled = true;
      this.#rejectReady(error);
      return;
    }

    for (const listener of this.#failureListeners) {
      listener(error);
    }
  }

  #rejectActiveTurn(error: AgyProcessControllerError): void {
    const active = this.#activeTurn;
    if (active === undefined) {
      return;
    }

    this.#activeTurn = undefined;
    clearTimeout(active.timer);
    active.reject(error);
  }

  #beginShutdown(): Promise<void> {
    if (this.#shutdownPromise !== undefined) {
      return this.#shutdownPromise;
    }

    this.#closed = true;
    this.#clearInitTimer();
    try {
      this.#child.stdin.end();
    } catch {
      // The process may already have closed its input stream.
    }

    this.#shutdownPromise = new Promise<void>((resolve) => {
      this.#resolveShutdown = resolve;
    });

    this.#signalBarrier = this.#sendSignal("SIGTERM");
    this.#terminationTimer = setTimeout(() => {
      void this.#startHardKill();
    }, this.#limits.cancelGraceMs);
    this.#terminationTimer.unref();

    return this.#shutdownPromise;
  }

  #startHardKill(): Promise<void> {
    if (this.#hardKillPromise !== undefined) {
      return this.#hardKillPromise;
    }
    if (this.#terminationTimer !== undefined) {
      clearTimeout(this.#terminationTimer);
      this.#terminationTimer = undefined;
    }

    const hardKill = this.#signalBarrier.then(() =>
      this.#sendSignal("SIGKILL"),
    );
    this.#signalBarrier = hardKill;
    this.#hardKillPromise = hardKill;
    void hardKill.then(() => {
      if (
        this.#resolveShutdown === undefined ||
        this.#hardKillTimer !== undefined
      ) {
        return;
      }
      this.#hardKillTimer = setTimeout(() => {
        this.#finalizeShutdown();
      }, this.#limits.hardKillGraceMs);
      this.#hardKillTimer.unref();
    });
    return hardKill;
  }

  async #sendSignal(signal: NodeJS.Signals): Promise<void> {
    try {
      await signalAgyProcessTree(this.#child, signal, {
        timeoutMs:
          signal === "SIGTERM"
            ? this.#limits.cancelGraceMs
            : this.#limits.hardKillGraceMs,
      });
    } catch {
      // A concurrent exit is equivalent to successful termination.
    }
  }

  #finalizeShutdown(): void {
    this.#clearShutdownTimers();
    this.#cleanupListeners();
    const resolve = this.#resolveShutdown;
    this.#resolveShutdown = undefined;
    resolve?.();
  }

  #clearShutdownTimers(): void {
    if (this.#terminationTimer !== undefined) {
      clearTimeout(this.#terminationTimer);
      this.#terminationTimer = undefined;
    }
    if (this.#hardKillTimer !== undefined) {
      clearTimeout(this.#hardKillTimer);
      this.#hardKillTimer = undefined;
    }
  }

  #cleanupListeners(): void {
    this.#startupSignal?.removeEventListener(
      "abort",
      this.#handleStartupAbort,
    );
    this.#child.stdout.off("data", this.#handleStdout);
    this.#child.stdout.off("end", this.#handleStdoutEnd);
    this.#child.stdin.off("error", this.#handleStdinError);
    this.#child.stderr.off("data", this.#handleStderr);
    this.#child.off("error", this.#handleProcessError);
    this.#child.off("exit", this.#handleExit);
    this.#child.off("close", this.#handleClose);
    this.#eventListeners.clear();
    this.#failureListeners.clear();
  }

  #clearInitTimer(): void {
    if (this.#initTimer !== undefined) {
      clearTimeout(this.#initTimer);
      this.#initTimer = undefined;
    }
  }
}

async function writeWithBackpressure(
  stream: Writable,
  line: string,
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let writeReturned = false;
    let callbackDone = false;
    let drainDone = false;
    let settled = false;

    const cleanup = (): void => {
      stream.off("drain", onDrain);
      stream.off("close", onClose);
    };
    const succeedIfReady = (): void => {
      if (!settled && writeReturned && callbackDone && drainDone) {
        settled = true;
        cleanup();
        resolve();
      }
    };
    const fail = (error: unknown): void => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(
        error instanceof Error ? error : new Error("Stream write failed"),
      );
    };
    const onDrain = (): void => {
      drainDone = true;
      succeedIfReady();
    };
    const onClose = (): void => {
      fail(new AgyProcessControllerError("PROCESS_CLOSED"));
    };
    const onWrite = (error?: Error | null): void => {
      if (error !== undefined && error !== null) {
        fail(error);
        return;
      }
      callbackDone = true;
      succeedIfReady();
    };

    stream.once("close", onClose);
    try {
      const accepted = stream.write(line, onWrite);
      writeReturned = true;
      drainDone = accepted;
      if (!accepted) {
        stream.once("drain", onDrain);
      }
      succeedIfReady();
    } catch (error) {
      fail(error);
    }
  });
}

function errorMessage(code: AgyProcessControllerErrorCode): string {
  switch (code) {
    case "SPAWN_FAILED":
      return "Failed to start agy process";
    case "INIT_TIMEOUT":
      return "agy process initialization timed out";
    case "PROCESS_EXITED":
      return "agy process exited unexpectedly";
    case "INVALID_OUTPUT":
      return "agy process produced invalid output";
    case "WRITE_FAILED":
      return "Failed to write to agy process";
    case "PROCESS_CLOSED":
      return "agy process is closed";
    case "PROCESS_BUSY":
      return "agy process is already running a turn";
    case "PROMPT_TIMEOUT":
      return "agy prompt timed out";
    case "CANCELLED":
      return "agy prompt was cancelled";
    case "EVENT_HANDLER_FAILED":
      return "agy event handler failed";
  }
}
