import {
  agent,
  methods,
  PROTOCOL_VERSION,
  RequestError,
  type AgentApp,
  type ContentBlock,
} from "@agentclientprotocol/sdk";

import type { AgyResultEvent } from "./agy-events.js";
import { AgyProcessConfigError } from "./agy-process.js";
import {
  AgyProcessControllerError,
  type AgyEventListener,
} from "./process-controller.js";
import {
  SessionManagerError,
  type CreateSessionOptions,
  type LoadSessionOptions,
} from "./session-manager.js";
const MAX_STREAMED_TEXT_BYTES = 4 * 1024 * 1024;

export interface AgySessionService {
  createSession(
    options: CreateSessionOptions,
    signal?: AbortSignal,
  ): Promise<string>;
  loadSession(
    options: LoadSessionOptions,
    signal?: AbortSignal,
  ): Promise<string>;
  prompt(
    sessionId: string,
    input: unknown,
    onEvent?: AgyEventListener,
  ): Promise<AgyResultEvent>;
  cancel(sessionId: string): Promise<void>;
  closeSession(sessionId: string): Promise<void>;
  closeAll(): Promise<void>;
}

export interface AgyUserInput {
  readonly event: "user";
  readonly message: {
    readonly role: "user";
    readonly content: readonly [
      {
        readonly type: "text";
        readonly text: string;
      },
    ];
  };
}

export function createAgyAgent(service: AgySessionService): AgentApp {
  return agent({ name: "agy-acp" })
    .onConnect((connection) => {
      connection.signal.addEventListener(
        "abort",
        () => {
          void service.closeAll().catch(() => undefined);
        },
        { once: true },
      );
    })
    .onRequest(methods.agent.initialize, ({ params }) => ({
      protocolVersion:
        params.protocolVersion === PROTOCOL_VERSION
          ? params.protocolVersion
          : PROTOCOL_VERSION,
      agentCapabilities: {
        promptCapabilities: {},
      },
      agentInfo: {
        name: "agy-acp",
        version: "0.1.0",
      },
    }))
    .onRequest(methods.agent.session.new, async ({ params, signal }) => {
      rejectMcpServers(params.mcpServers);
      try {
        const sessionId = await service.createSession(
          createOptions(params),
          signal,
        );
        if (signal.aborted) {
          await service.closeSession(sessionId).catch(() => undefined);
          throw RequestError.requestCancelled();
        }
        return { sessionId };
      } catch (error) {
        if (signal.aborted) {
          throw RequestError.requestCancelled();
        }
        throw toRequestError(error);
      }
    })
    .onRequest(methods.agent.session.load, async ({ params, signal }) => {
      rejectMcpServers(params.mcpServers);
      try {
        const sessionId = await service.loadSession(loadOptions(params), signal);
        if (signal.aborted) {
          await service.closeSession(sessionId).catch(() => undefined);
          throw RequestError.requestCancelled();
        }
        return {};
      } catch (error) {
        if (signal.aborted) {
          throw RequestError.requestCancelled();
        }
        throw toRequestError(error);
      }
    })
    .onRequest(methods.agent.session.close, async ({ params }) => {
      await mapRequestError(() => service.closeSession(params.sessionId));
    })
    .onNotification(methods.agent.session.cancel, async ({ params }) => {
      try {
        await service.cancel(params.sessionId);
      } catch (error) {
        if (
          !(error instanceof SessionManagerError) ||
          error.code !== "UNKNOWN_SESSION"
        ) {
          throw toRequestError(error);
        }
      }
    })
    .onRequest(
      methods.agent.session.prompt,
      async ({ params, client, signal }) => {
      const input = promptToAgyInput(params.prompt);
      let streamedText = "";
      let streamedTextBytes = 0;
      const cancelForAbort = (): void => {
        void service.cancel(params.sessionId).catch(() => undefined);
      };
      signal.addEventListener("abort", cancelForAbort, { once: true });
      if (signal.aborted) {
        cancelForAbort();
      }

      try {
        const result = await service.prompt(
          params.sessionId,
          input,
          async (event) => {
            if (
              event.kind !== "step_update" ||
              event.textDelta === undefined ||
              event.textDelta.length === 0
            ) {
              return;
            }

            streamedTextBytes += Buffer.byteLength(event.textDelta);
            if (streamedTextBytes > MAX_STREAMED_TEXT_BYTES) {
              throw RequestError.internalError(
                undefined,
                "agy streamed response exceeded limit",
              );
            }
            streamedText += event.textDelta;
            await client.notify(methods.client.session.update, {
              sessionId: params.sessionId,
              update: {
                sessionUpdate: "agent_message_chunk",
                content: { type: "text", text: event.textDelta },
              },
            });
          },
        );

        if (result.status === "SUCCESS") {
          if (!result.response.startsWith(streamedText)) {
            throw RequestError.internalError(
              undefined,
              "agy streamed response mismatch",
            );
          }
          const suffix = result.response.slice(streamedText.length);
          if (suffix.length > 0) {
            await client.notify(methods.client.session.update, {
              sessionId: params.sessionId,
              update: {
                sessionUpdate: "agent_message_chunk",
                content: { type: "text", text: suffix },
              },
            });
          }
          return { stopReason: "end_turn" };
        }
        throw RequestError.internalError(undefined, "agy execution failed");
      } catch (error) {
        if (signal.aborted) {
          throw RequestError.requestCancelled();
        }
        if (
          error instanceof AgyProcessControllerError &&
          error.code === "CANCELLED"
        ) {
          return { stopReason: "cancelled" };
        }
        throw toRequestError(error);
      } finally {
        signal.removeEventListener("abort", cancelForAbort);
      }
      },
    );
}

export function promptToAgyInput(
  blocks: readonly ContentBlock[],
): AgyUserInput {
  const parts: string[] = [];

  for (const block of blocks) {
    switch (block.type) {
      case "text":
        if (block.text.length > 0) {
          parts.push(block.text);
        }
        break;
      case "resource_link":
        parts.push(`Resource: ${block.name} (${block.uri})`);
        break;
      case "image":
      case "audio":
      case "resource":
        throw RequestError.invalidParams(
          undefined,
          `Unsupported prompt content type: ${block.type}`,
        );
    }
  }

  const text = parts.join("\n\n");
  if (text.length === 0) {
    throw RequestError.invalidParams(undefined, "Prompt must contain content");
  }

  return {
    event: "user",
    message: {
      role: "user",
      content: [{ type: "text", text }],
    },
  };
}

function rejectMcpServers(mcpServers: readonly unknown[]): void {
  if (mcpServers.length > 0) {
    throw RequestError.invalidParams(
      undefined,
      "Client-provided MCP servers are not supported",
    );
  }
}

function createOptions(params: {
  readonly cwd: string;
  readonly additionalDirectories?: string[];
}): CreateSessionOptions {
  return params.additionalDirectories === undefined
    ? { cwd: params.cwd }
    : {
        cwd: params.cwd,
        additionalDirectories: params.additionalDirectories,
      };
}

function loadOptions(params: {
  readonly cwd: string;
  readonly sessionId: string;
  readonly additionalDirectories?: string[];
}): LoadSessionOptions {
  return params.additionalDirectories === undefined
    ? { cwd: params.cwd, sessionId: params.sessionId }
    : {
        cwd: params.cwd,
        sessionId: params.sessionId,
        additionalDirectories: params.additionalDirectories,
      };
}

async function mapRequestError<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    throw toRequestError(error);
  }
}

function toRequestError(error: unknown): RequestError {
  if (error instanceof RequestError) {
    return error;
  }
  if (error instanceof AgyProcessConfigError) {
    return RequestError.invalidParams(undefined, error.message);
  }
  if (error instanceof SessionManagerError) {
    switch (error.code) {
      case "UNKNOWN_SESSION":
      case "INVALID_SESSION":
        return RequestError.invalidParams(undefined, error.message);
      case "SESSION_BUSY":
        return new RequestError(-32010, error.message);
      case "SESSION_EXISTS":
        return new RequestError(-32011, error.message);
      case "SESSION_LIMIT":
        return new RequestError(-32000, error.message);
      case "SESSION_ID_MISMATCH":
      case "MANAGER_CLOSED":
        return RequestError.internalError(undefined, error.message);
    }
  }
  if (error instanceof AgyProcessControllerError) {
    return RequestError.internalError(undefined, error.message);
  }
  return RequestError.internalError(undefined, "agy-acp request failed");
}
