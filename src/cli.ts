#!/usr/bin/env node

import { ndJsonStream } from "@agentclientprotocol/sdk";
import { parseArgs } from "node:util";
import { Readable, Writable } from "node:stream";

import { createAgyAgent } from "./acp-agent.js";
import { DEFAULT_LIMITS, type RuntimeLimits } from "./config.js";
import { SessionManager } from "./session-manager.js";

export interface AgyAcpServerOptions {
  readonly input: Readable;
  readonly output: Writable;
  readonly env: NodeJS.ProcessEnv;
  readonly agyPath?: string;
  readonly limits?: RuntimeLimits;
  readonly signal?: AbortSignal;
}

export async function runAgyAcpServer(
  options: AgyAcpServerOptions,
): Promise<void> {
  const limits = options.limits ?? DEFAULT_LIMITS;
  const manager = new SessionManager({
    limits,
    executable: options.agyPath ?? options.env.AGY_ACP_AGY_PATH ?? "agy",
    env: options.env,
  });
  const stream = ndJsonStream(
    Writable.toWeb(options.output) as WritableStream<Uint8Array>,
    Readable.toWeb(options.input) as ReadableStream<Uint8Array>,
    { maxMessageBytes: limits.maxLineBytes },
  );
  const connection = createAgyAgent(manager).connect(stream);
  const closeConnection = (): void => connection.close();

  if (options.signal?.aborted === true) {
    closeConnection();
  } else {
    options.signal?.addEventListener("abort", closeConnection, { once: true });
  }

  try {
    await connection.closed;
  } finally {
    options.signal?.removeEventListener("abort", closeConnection);
    await manager.closeAll();
  }
}

export async function main(args = process.argv.slice(2)): Promise<void> {
  const parsed = parseArgs({
    args,
    allowPositionals: false,
    strict: true,
    options: {
      "agy-path": { type: "string" },
    },
  });
  const abort = new AbortController();
  const stop = (): void => abort.abort();
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);

  try {
    const serverOptions: AgyAcpServerOptions = {
      input: process.stdin,
      output: process.stdout,
      env: process.env,
      signal: abort.signal,
    };
    const agyPath = parsed.values["agy-path"];
    await runAgyAcpServer(
      agyPath === undefined ? serverOptions : { ...serverOptions, agyPath },
    );
  } finally {
    process.off("SIGINT", stop);
    process.off("SIGTERM", stop);
  }
}

void main().catch(() => {
  process.stderr.write("agy-acp: fatal server error\n");
  process.exitCode = 1;
});
