import type { ChildProcessWithoutNullStreams } from "node:child_process";
import type { Writable } from "node:stream";

import { parseAgyEvent, type AgyEvent } from "./agy-events.js";
import {
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
  | "EVENT_HANDLER_FAILED";

export interface AgyProcessControllerOptions {
  readonly invocation: AgyInvocationOptions;
  readonly limits: RuntimeLimits;
}

export interface AgyProcessDiagnostics {
  readonly stderrBytes: number;
  readonly stderrTruncated: boolean;
}

export class AgyProcessControllerError extends Error {
  public readonly code: AgyProcessControllerErrorCode;

  public constructor(code: AgyProcessControllerErrorCode, cause?: unknown) {
    super(errorMessage(code), cause === undefined ? undefined : { cause });
    this.name = "AgyProcessControllerError";
    this.code = code;
  }
}

export class AgyProcessController {
  readonly #child: ChildProcessWithoutNullStreams;
  readonly #limits: RuntimeLimits;
  readonly #parser: NdjsonParser<unknown>;
  readonly #eventListeners = new Set<(event: AgyEvent) => void>();
  readonly #failureListeners = new Set<
    (error: AgyProcessControllerError) => void
  >();
  readonly #ready: Promise<void>;
  readonly #resolveReady: () => void;
  readonly #rejectReady: (error: AgyProcessControllerError) => void;
  #initTimer: NodeJS.Timeout | undefined;
  #conversationId: string | undefined;
  #stderr = Buffer.alloc(0);
  #stderrTruncated = false;
  #readySettled = false;
  #failed = false;
  #closed = false;

  private constructor(
    child: ChildProcessWithoutNullStreams,
    limits: RuntimeLimits,
  ) {
    this.#child = child;
    this.#limits = limits;
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
    child.stderr.on("data", this.#handleStderr);
    child.on("error", this.#handleProcessError);
    child.on("exit", this.#handleExit);

    this.#initTimer = setTimeout(() => {
      this.#fail(new AgyProcessControllerError("INIT_TIMEOUT"));
    }, limits.initTimeoutMs);
    this.#initTimer.unref();
  }

  public static async start(
    options: AgyProcessControllerOptions,
    spawn?: AgySpawnFunction,
  ): Promise<AgyProcessController> {
    let child: ChildProcessWithoutNullStreams;
    try {
      child = spawnAgyProcess(options.invocation, spawn);
    } catch (error) {
      throw new AgyProcessControllerError("SPAWN_FAILED", error);
    }

    const controller = new AgyProcessController(child, options.limits);
    await controller.#ready;
    return controller;
  }

  public get conversationId(): string {
    if (this.#conversationId === undefined) {
      throw new AgyProcessControllerError("PROCESS_CLOSED");
    }
    return this.#conversationId;
  }

  public get diagnostics(): AgyProcessDiagnostics {
    return {
      stderrBytes: this.#stderr.byteLength,
      stderrTruncated: this.#stderrTruncated,
    };
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

  public async send(value: unknown): Promise<void> {
    if (this.#closed || this.#failed || this.#conversationId === undefined) {
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

    let accepted: boolean;
    try {
      accepted = this.#child.stdin.write(line);
    } catch (error) {
      throw new AgyProcessControllerError("WRITE_FAILED", error);
    }

    if (!accepted) {
      await waitForDrain(this.#child.stdin);
    }
  }

  readonly #handleStdout = (chunk: string | Uint8Array): void => {
    if (this.#failed || this.#closed) {
      return;
    }

    try {
      for (const value of this.#parser.push(chunk)) {
        this.#acceptEvent(parseAgyEvent(value));
      }
    } catch (error) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT", error));
    }
  };

  readonly #handleStdoutEnd = (): void => {
    if (this.#failed || this.#closed) {
      return;
    }

    try {
      for (const value of this.#parser.finish()) {
        this.#acceptEvent(parseAgyEvent(value));
      }
    } catch (error) {
      this.#fail(new AgyProcessControllerError("INVALID_OUTPUT", error));
    }
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

  readonly #handleExit = (): void => {
    this.#closed = true;
    if (!this.#failed) {
      this.#fail(new AgyProcessControllerError("PROCESS_EXITED"), false);
    }
  };

  #acceptEvent(event: AgyEvent): void {
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

    for (const listener of this.#eventListeners) {
      try {
        listener(event);
      } catch (error) {
        this.#fail(
          new AgyProcessControllerError("EVENT_HANDLER_FAILED", error),
        );
        return;
      }
    }
  }

  #fail(error: AgyProcessControllerError, terminate = true): void {
    if (this.#failed) {
      return;
    }

    this.#failed = true;
    this.#clearInitTimer();
    if (terminate && !this.#closed) {
      this.#child.kill("SIGTERM");
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

  #clearInitTimer(): void {
    if (this.#initTimer !== undefined) {
      clearTimeout(this.#initTimer);
      this.#initTimer = undefined;
    }
  }
}

async function waitForDrain(stream: Writable): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const cleanup = (): void => {
      stream.off("drain", onDrain);
      stream.off("error", onError);
      stream.off("close", onClose);
    };
    const onDrain = (): void => {
      cleanup();
      resolve();
    };
    const onError = (error: Error): void => {
      cleanup();
      reject(new AgyProcessControllerError("WRITE_FAILED", error));
    };
    const onClose = (): void => {
      cleanup();
      reject(new AgyProcessControllerError("PROCESS_CLOSED"));
    };

    stream.once("drain", onDrain);
    stream.once("error", onError);
    stream.once("close", onClose);
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
    case "EVENT_HANDLER_FAILED":
      return "agy event handler failed";
  }
}
