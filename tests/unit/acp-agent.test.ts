import {
  client,
  methods,
  PROTOCOL_VERSION,
  type SessionNotification,
} from "@agentclientprotocol/sdk";
import { describe, expect, it } from "vitest";
import { setImmediate as immediate } from "node:timers/promises";

import type { AgyResultEvent } from "../../src/agy-events.js";
import { AgyProcessConfigError } from "../../src/agy-process.js";
import {
  createAgyAgent,
  type AgySessionService,
} from "../../src/acp-agent.js";
import {
  AgyProcessControllerError,
  type AgyEventListener,
} from "../../src/process-controller.js";
import {
  SessionManagerError,
  type CreateSessionOptions,
  type LoadSessionOptions,
} from "../../src/session-manager.js";

function successResult(sessionId = "session-1"): AgyResultEvent {
  return {
    kind: "result",
    conversationId: sessionId,
    durationSeconds: 1,
    error: null,
    numTurns: 1,
    response: "delta",
    status: "SUCCESS",
    usage: {},
  };
}

class FakeSessionService implements AgySessionService {
  public readonly creates: CreateSessionOptions[] = [];
  public readonly createSignals: Array<AbortSignal | undefined> = [];
  public readonly loads: LoadSessionOptions[] = [];
  public readonly loadSignals: Array<AbortSignal | undefined> = [];
  public readonly prompts: Array<{ sessionId: string; input: unknown }> = [];
  public readonly cancellations: string[] = [];
  public readonly closes: string[] = [];
  public closeAllCount = 0;
  public createError: Error | undefined;
  public loadError: Error | undefined;
  public promptError: Error | undefined;
  public holdPrompt = false;
  public rejectHeldPrompt: ((error: Error) => void) | undefined;
  public promptResult = successResult();

  public createSession(
    options: CreateSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.creates.push(options);
    this.createSignals.push(signal);
    return this.createError === undefined
      ? Promise.resolve("session-1")
      : Promise.reject(this.createError);
  }

  public loadSession(
    options: LoadSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.loads.push(options);
    this.loadSignals.push(signal);
    return this.loadError === undefined
      ? Promise.resolve(options.sessionId)
      : Promise.reject(this.loadError);
  }

  public prompt(
    sessionId: string,
    input: unknown,
    onEvent?: AgyEventListener,
  ): Promise<AgyResultEvent> {
    this.prompts.push({ sessionId, input });
    const emitted = onEvent?.({
      kind: "step_update",
      conversationId: sessionId,
      stepIndex: 0,
      state: "running",
      stepType: "assistant_text",
      textDelta: "delta",
      durationSeconds: undefined,
      usage: undefined,
    });
    return Promise.resolve(emitted).then(() => {
      if (this.holdPrompt) {
        return new Promise<AgyResultEvent>((_resolve, reject) => {
          this.rejectHeldPrompt = reject;
        });
      }
      if (this.promptError !== undefined) {
        throw this.promptError;
      }
      return { ...this.promptResult, conversationId: sessionId };
    });
  }

  public cancel(sessionId: string): Promise<void> {
    this.cancellations.push(sessionId);
    this.rejectHeldPrompt?.(new AgyProcessControllerError("CANCELLED"));
    this.rejectHeldPrompt = undefined;
    return Promise.resolve();
  }

  public closeSession(sessionId: string): Promise<void> {
    this.closes.push(sessionId);
    return Promise.resolve();
  }

  public closeAll(): Promise<void> {
    this.closeAllCount += 1;
    return Promise.resolve();
  }
}

describe("createAgyAgent", () => {
  it("negotiates ACP v1 and advertises only implemented capabilities", async () => {
    const service = new FakeSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      const response = await context.request(methods.agent.initialize, {
        protocolVersion: PROTOCOL_VERSION,
        clientCapabilities: {},
      });

      expect(response).toMatchObject({
        protocolVersion: PROTOCOL_VERSION,
        agentInfo: { name: "agy-acp", version: "0.1.0" },
        agentCapabilities: {
          promptCapabilities: {},
        },
      });
      expect(response.agentCapabilities).not.toHaveProperty("loadSession");
      expect(response.agentCapabilities).not.toHaveProperty(
        "sessionCapabilities",
      );
      expect(response.agentCapabilities).not.toHaveProperty("mcpCapabilities");
    });
  });

  it("routes new, load, close and cancel without accepting MCP servers", async () => {
    const service = new FakeSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      await expect(
        context.request(methods.agent.session.new, {
          cwd: "/workspace",
          additionalDirectories: ["/shared"],
          mcpServers: [],
        }),
      ).resolves.toEqual({ sessionId: "session-1" });
      await context.request(methods.agent.session.load, {
        cwd: "/workspace",
        sessionId: "existing-id",
        mcpServers: [],
      });
      await context.notify(methods.agent.session.cancel, {
        sessionId: "session-1",
      });
      await context.request(methods.agent.session.close, {
        sessionId: "session-1",
      });

      expect(service.creates).toEqual([
        { cwd: "/workspace", additionalDirectories: ["/shared"] },
      ]);
      expect(service.loads).toEqual([
        { cwd: "/workspace", sessionId: "existing-id" },
      ]);
      expect(service.cancellations).toEqual(["session-1"]);
      expect(service.closes).toEqual(["session-1"]);

      await expect(
        context.request(methods.agent.session.new, {
          cwd: "/workspace",
          mcpServers: [
            { name: "server", command: "/bin/server", args: [], env: [] },
          ],
        }),
      ).rejects.toMatchObject({ code: -32602 });
    });
  });

  it("converts baseline prompt blocks and streams text updates", async () => {
    const service = new FakeSessionService();
    const updates: SessionNotification[] = [];
    const app = createAgyAgent(service);
    const testClient = client({ name: "test-client" }).onNotification(
      methods.client.session.update,
      ({ params }) => {
        updates.push(params);
      },
    );

    await testClient.connectWith(app, async (context) => {
      await expect(
        context.request(methods.agent.session.prompt, {
          sessionId: "session-1",
          prompt: [
            { type: "text", text: "hello" },
            {
              type: "resource_link",
              name: "guide",
              uri: "file:///guide.md",
            },
          ],
        }),
      ).resolves.toEqual({ stopReason: "end_turn" });
    });

    expect(service.prompts).toEqual([
      {
        sessionId: "session-1",
        input: {
          event: "user",
          message: {
            role: "user",
            content: [
              {
                type: "text",
                text: "hello\n\nResource: guide (file:///guide.md)",
              },
            ],
          },
        },
      },
    ]);
    expect(updates).toEqual([
      {
        sessionId: "session-1",
        update: {
          sessionUpdate: "agent_message_chunk",
          content: { type: "text", text: "delta" },
        },
      },
    ]);
  });

  it("rejects unsupported content and maps cancellation", async () => {
    const service = new FakeSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      await expect(
        context.request(methods.agent.session.prompt, {
          sessionId: "session-1",
          prompt: [{ type: "image", data: "AA==", mimeType: "image/png" }],
        }),
      ).rejects.toMatchObject({ code: -32602 });

      service.promptError = new AgyProcessControllerError("CANCELLED");
      await expect(
        context.request(methods.agent.session.prompt, {
          sessionId: "session-1",
          prompt: [{ type: "text", text: "cancel me" }],
        }),
      ).resolves.toEqual({ stopReason: "cancelled" });
    });
  });
});

describe("createAgyAgent request boundaries", () => {
  it("cancels the session when the SDK prompt request signal aborts", async () => {
    const service = new FakeSessionService();
    service.holdPrompt = true;
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      const abort = new AbortController();
      const prompting = context.request(
        methods.agent.session.prompt,
        {
          sessionId: "session-1",
          prompt: [{ type: "text", text: "cancel request" }],
        },
        { cancellationSignal: abort.signal },
      );
      await immediate();
      expect(service.rejectHeldPrompt).toBeDefined();
      abort.abort();

      await expect(prompting).rejects.toMatchObject({ code: -32800 });
      await Promise.resolve();
      expect(service.cancellations).toEqual(["session-1"]);
    });
  });

  it("maps client configuration errors separately from backend identity errors", async () => {
    const service = new FakeSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      service.createError = new AgyProcessConfigError("cwd");
      await expect(
        context.request(methods.agent.session.new, {
          cwd: "relative",
          mcpServers: [],
        }),
      ).rejects.toMatchObject({ code: -32602 });

      service.createError = undefined;
      service.loadError = new SessionManagerError("SESSION_ID_MISMATCH");
      await expect(
        context.request(methods.agent.session.load, {
          cwd: "/workspace",
          sessionId: "opaque-id",
          mcpServers: [],
        }),
      ).rejects.toMatchObject({ code: -32603 });
    });
  });

  it("does not infer client cancellation from backend error text", async () => {
    const service = new FakeSessionService();
    service.promptResult = {
      ...successResult(),
      status: "ERROR",
      error: "provider context canceled unexpectedly",
      response: "",
    };
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      await expect(
        context.request(methods.agent.session.prompt, {
          sessionId: "session-1",
          prompt: [{ type: "text", text: "backend failure" }],
        }),
      ).rejects.toMatchObject({ code: -32603 });
    });
  });

  it("maps transient session state conflicts to retryable server errors", async () => {
    const service = new FakeSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      service.createError = new SessionManagerError("SESSION_BUSY");
      await expect(
        context.request(methods.agent.session.new, {
          cwd: "/workspace",
          mcpServers: [],
        }),
      ).rejects.toMatchObject({ code: -32010 });

      service.createError = new SessionManagerError("SESSION_EXISTS");
      await expect(
        context.request(methods.agent.session.new, {
          cwd: "/workspace",
          mcpServers: [],
        }),
      ).rejects.toMatchObject({ code: -32011 });
    });
  });
});

interface Deferred<T> {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

class DelayedSessionService extends FakeSessionService {
  public readonly createStart = deferred<string>();
  public readonly loadStart = deferred<string>();

  public override createSession(
    options: CreateSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.creates.push(options);
    this.createSignals.push(signal);
    return this.createStart.promise;
  }

  public override loadSession(
    options: LoadSessionOptions,
    signal?: AbortSignal,
  ): Promise<string> {
    this.loads.push(options);
    this.loadSignals.push(signal);
    return this.loadStart.promise;
  }
}

describe("createAgyAgent startup cancellation", () => {
  it("closes a late new session after request cancellation", async () => {
    const service = new DelayedSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      const abort = new AbortController();
      const creating = context.request(
        methods.agent.session.new,
        { cwd: "/workspace", mcpServers: [] },
        { cancellationSignal: abort.signal },
      );
      await immediate();
      const handlerSignal = service.createSignals[0];
      expect(handlerSignal?.aborted).toBe(false);
      abort.abort();
      await immediate();
      expect(handlerSignal?.aborted).toBe(true);
      service.createStart.resolve("late-new");

      await expect(creating).rejects.toMatchObject({ code: -32800 });
      expect(service.closes).toEqual(["late-new"]);
    });
  });

  it("closes a late loaded session after request cancellation", async () => {
    const service = new DelayedSessionService();
    const app = createAgyAgent(service);

    await client({ name: "test-client" }).connectWith(app, async (context) => {
      const abort = new AbortController();
      const loading = context.request(
        methods.agent.session.load,
        { cwd: "/workspace", sessionId: "late-load", mcpServers: [] },
        { cancellationSignal: abort.signal },
      );
      await immediate();
      const handlerSignal = service.loadSignals[0];
      expect(handlerSignal?.aborted).toBe(false);
      abort.abort();
      await immediate();
      expect(handlerSignal?.aborted).toBe(true);
      service.loadStart.resolve("late-load");

      await expect(loading).rejects.toMatchObject({ code: -32800 });
      expect(service.closes).toEqual(["late-load"]);
    });
  });
});

describe("createAgyAgent streamed response reconciliation", () => {
  it("emits only the verified suffix from the authoritative final response", async () => {
    const service = new FakeSessionService();
    service.promptResult = { ...successResult(), response: "delta-tail" };
    const updates: string[] = [];
    const app = createAgyAgent(service);
    const testClient = client({ name: "test-client" }).onNotification(
      methods.client.session.update,
      ({ params }) => {
        if (
          params.update.sessionUpdate === "agent_message_chunk" &&
          params.update.content.type === "text"
        ) {
          updates.push(params.update.content.text);
        }
      },
    );

    await testClient.connectWith(app, async (context) => {
      await context.request(methods.agent.session.prompt, {
        sessionId: "session-1",
        prompt: [{ type: "text", text: "suffix" }],
      });
    });

    expect(updates).toEqual(["delta", "-tail"]);
  });

  it("fails closed when the final response is incompatible with streamed text", async () => {
    const service = new FakeSessionService();
    service.promptResult = { ...successResult(), response: "different" };
    const updates: string[] = [];
    const app = createAgyAgent(service);
    const testClient = client({ name: "test-client" }).onNotification(
      methods.client.session.update,
      ({ params }) => {
        if (
          params.update.sessionUpdate === "agent_message_chunk" &&
          params.update.content.type === "text"
        ) {
          updates.push(params.update.content.text);
        }
      },
    );

    await testClient.connectWith(app, async (context) => {
      await expect(
        context.request(methods.agent.session.prompt, {
          sessionId: "session-1",
          prompt: [{ type: "text", text: "mismatch" }],
        }),
      ).rejects.toMatchObject({ code: -32603 });
    });

    expect(updates).toEqual(["delta"]);
  });
});
