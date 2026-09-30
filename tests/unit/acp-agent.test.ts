import {
  client,
  methods,
  PROTOCOL_VERSION,
  type SessionNotification,
} from "@agentclientprotocol/sdk";
import { describe, expect, it } from "vitest";

import type { AgyEvent, AgyResultEvent } from "../../src/agy-events.js";
import {
  createAgyAgent,
  type AgySessionService,
} from "../../src/acp-agent.js";
import { AgyProcessControllerError } from "../../src/process-controller.js";
import type {
  CreateSessionOptions,
  LoadSessionOptions,
} from "../../src/session-manager.js";

function successResult(sessionId = "session-1"): AgyResultEvent {
  return {
    kind: "result",
    conversationId: sessionId,
    durationSeconds: 1,
    error: null,
    numTurns: 1,
    response: "done",
    status: "SUCCESS",
    usage: {},
  };
}

class FakeSessionService implements AgySessionService {
  public readonly creates: CreateSessionOptions[] = [];
  public readonly loads: LoadSessionOptions[] = [];
  public readonly prompts: Array<{ sessionId: string; input: unknown }> = [];
  public readonly cancellations: string[] = [];
  public readonly closes: string[] = [];
  public closeAllCount = 0;
  public promptError: Error | undefined;
  public promptResult = successResult();

  public createSession(options: CreateSessionOptions): Promise<string> {
    this.creates.push(options);
    return Promise.resolve("session-1");
  }

  public loadSession(options: LoadSessionOptions): Promise<string> {
    this.loads.push(options);
    return Promise.resolve(options.sessionId);
  }

  public prompt(
    sessionId: string,
    input: unknown,
    onEvent?: (event: AgyEvent) => void,
  ): Promise<AgyResultEvent> {
    this.prompts.push({ sessionId, input });
    onEvent?.({
      kind: "step_update",
      conversationId: sessionId,
      stepIndex: 0,
      state: "running",
      stepType: "assistant_text",
      textDelta: "delta",
      durationSeconds: undefined,
      usage: undefined,
    });
    if (this.promptError !== undefined) {
      return Promise.reject(this.promptError);
    }
    return Promise.resolve({ ...this.promptResult, conversationId: sessionId });
  }

  public cancel(sessionId: string): Promise<void> {
    this.cancellations.push(sessionId);
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
        agentInfo: { name: "agy-acp", version: "0.0.0-development" },
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
