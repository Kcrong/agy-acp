import {
  client,
  methods,
  ndJsonStream,
  PROTOCOL_VERSION,
  type ClientContext,
  type SessionNotification,
} from "@agentclientprotocol/sdk";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { Readable, Writable } from "node:stream";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const CLI_PATH = fileURLToPath(new URL("../../dist/cli.js", import.meta.url));
const FAKE_AGY_PATH = fileURLToPath(
  new URL("../fixtures/fake-agy.mjs", import.meta.url),
);

interface HarnessContext {
  readonly agent: ClientContext;
  readonly updates: SessionNotification[];
}

async function within<T>(promise: Promise<T>, stage: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error(`E2E timeout at ${stage}`)), 3_000);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

async function waitUntil(predicate: () => boolean): Promise<void> {
  while (!predicate()) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

async function withCli(
  mode: string,
  operation: (context: HarnessContext) => Promise<void>,
  environment: NodeJS.ProcessEnv = {},
): Promise<void> {
  const child = spawn(
    process.execPath,
    [CLI_PATH, "--agy-path", FAKE_AGY_PATH],
    {
      cwd: process.cwd(),
      env: {
        FAKE_AGY_MODE: mode,
        HOME: process.env.HOME,
        LANG: "C.UTF-8",
        PATH: process.env.PATH,
        ...environment,
      },
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    },
  );
  const exited = once(child, "exit");
  const stderr: Buffer[] = [];
  child.stderr.on("data", (chunk: Buffer) => stderr.push(Buffer.from(chunk)));
  const updates: SessionNotification[] = [];
  const testClient = client({ name: "e2e-client" }).onNotification(
    methods.client.session.update,
    ({ params }) => {
      updates.push(params);
    },
  );
  const connection = testClient.connect(
    ndJsonStream(
      Writable.toWeb(child.stdin) as WritableStream<Uint8Array>,
      Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>,
    ),
  );

  try {
    await within(
      connection.agent.request(methods.agent.initialize, {
        protocolVersion: PROTOCOL_VERSION,
        clientCapabilities: {},
      }),
      "initialize",
    );
    await operation({ agent: connection.agent, updates });
  } finally {
    connection.close();
    child.stdin.end();
    try {
      const [code, signal] = (await within(exited, "CLI exit")) as [
        number | null,
        NodeJS.Signals | null,
      ];
      expect({ code, signal }).toEqual({ code: 0, signal: null });
      expect(stderr).toEqual([]);
    } finally {
      if (child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
      }
    }
  }
}

async function newSession(agent: ClientContext): Promise<string> {
  const response = await within(
    agent.request(methods.agent.session.new, {
      cwd: process.cwd(),
      mcpServers: [],
    }),
    "session/new",
  );
  return response.sessionId;
}

async function closeSession(
  agent: ClientContext,
  sessionId: string,
): Promise<void> {
  await within(
    agent.request(methods.agent.session.close, { sessionId }),
    "session/close",
  );
}

describe("agy-acp failure matrix", () => {
  it("parses init and streaming events split across byte chunks", async () => {
    await withCli("split", async ({ agent, updates }) => {
      const sessionId = await newSession(agent);
      await expect(
        within(
          agent.request(methods.agent.session.prompt, {
            sessionId,
            prompt: [{ type: "text", text: "split" }],
          }),
          "split prompt",
        ),
      ).resolves.toEqual({ stopReason: "end_turn" });
      expect(updates).toHaveLength(1);
      await closeSession(agent, sessionId);
    });
  });

  it.each(["malformed", "early-exit"])(
    "returns an internal protocol error for %s child output",
    async (mode) => {
      await withCli(mode, async ({ agent }) => {
        const sessionId = await newSession(agent);
        await expect(
          within(
            agent.request(methods.agent.session.prompt, {
              sessionId,
              prompt: [{ type: "text", text: "fail" }],
            }),
            `${mode} prompt`,
          ),
        ).rejects.toMatchObject({ code: -32603 });
        await closeSession(agent, sessionId);
      });
    },
  );

  it("times out a hanging prompt and terminates its child", async () => {
    await withCli(
      "hang",
      async ({ agent }) => {
        const sessionId = await newSession(agent);
        await expect(
          within(
            agent.request(methods.agent.session.prompt, {
              sessionId,
              prompt: [{ type: "text", text: "timeout" }],
            }),
            "timeout prompt",
          ),
        ).rejects.toMatchObject({ code: -32603 });
        await closeSession(agent, sessionId);
      },
      {
        AGY_ACP_PROMPT_TIMEOUT_MS: "30",
        AGY_ACP_CANCEL_GRACE_MS: "20",
        AGY_ACP_HARD_KILL_GRACE_MS: "20",
      },
    );
  });

  it("cancels a running prompt and returns the ACP cancelled reason", async () => {
    await withCli("hang", async ({ agent, updates }) => {
      const sessionId = await newSession(agent);
      const prompting = agent.request(methods.agent.session.prompt, {
        sessionId,
        prompt: [{ type: "text", text: "cancel" }],
      });
      await within(
        waitUntil(() => updates.length > 0),
        "cancel prompt start",
      );

      await within(
        agent.notify(methods.agent.session.cancel, { sessionId }),
        "session/cancel",
      );
      await expect(within(prompting, "cancelled prompt")).resolves.toEqual({
        stopReason: "cancelled",
      });
      await closeSession(agent, sessionId);
    });
  });

  it("runs two sessions concurrently on independent child processes", async () => {
    await withCli("normal", async ({ agent, updates }) => {
      const [first, second] = await Promise.all([
        newSession(agent),
        newSession(agent),
      ]);
      expect(first).not.toBe(second);

      await expect(
        Promise.all([
          agent.request(methods.agent.session.prompt, {
            sessionId: first,
            prompt: [{ type: "text", text: "first" }],
          }),
          agent.request(methods.agent.session.prompt, {
            sessionId: second,
            prompt: [{ type: "text", text: "second" }],
          }),
        ]),
      ).resolves.toEqual([
        { stopReason: "end_turn" },
        { stopReason: "end_turn" },
      ]);
      expect(updates).toHaveLength(2);
      await Promise.all([
        closeSession(agent, first),
        closeSession(agent, second),
      ]);
    });
  });
});
