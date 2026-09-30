import {
  client,
  methods,
  ndJsonStream,
  PROTOCOL_VERSION,
} from "@agentclientprotocol/sdk";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { Readable, Writable } from "node:stream";
import { fileURLToPath } from "node:url";

import { expect, it } from "vitest";

const CLI_PATH = fileURLToPath(new URL("../../dist/cli.js", import.meta.url));
const realIt = process.env.RUN_REAL_AGY_E2E === "1" ? it : it.skip;

async function within<T>(
  promise: Promise<T>,
  stage: string,
  timeoutMs: number,
): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error(`Real agy E2E timeout at ${stage}`)),
      timeoutMs,
    );
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  }
}

realIt(
  "runs an authenticated prompt through the installed agy without exposing data",
  async () => {
    const agyPath = process.env.AGY_E2E_AGY_PATH ?? "agy";
    const child = spawn(process.execPath, [CLI_PATH, "--agy-path", agyPath], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        AGY_ACP_PROMPT_TIMEOUT_MS: "120000",
      },
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const exited = once(child, "exit");
    let stderrBytes = 0;
    child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.byteLength;
    });
    let responseText = "";
    const testClient = client({ name: "real-smoke-client" }).onNotification(
      methods.client.session.update,
      ({ params }) => {
        if (
          params.update.sessionUpdate === "agent_message_chunk" &&
          params.update.content.type === "text"
        ) {
          responseText += params.update.content.text;
        }
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
        10_000,
      );
      const session = await within(
        connection.agent.request(methods.agent.session.new, {
          cwd: process.cwd(),
          mcpServers: [],
        }),
        "session/new",
        20_000,
      );
      await expect(
        within(
          connection.agent.request(methods.agent.session.prompt, {
            sessionId: session.sessionId,
            prompt: [
              {
                type: "text",
                text: "Reply exactly AGY_ACP_REAL_OK and do not use tools.",
              },
            ],
          }),
          "session/prompt",
          120_000,
        ),
      ).resolves.toEqual({ stopReason: "end_turn" });
      await within(
        connection.agent.request(methods.agent.session.close, {
          sessionId: session.sessionId,
        }),
        "session/close",
        10_000,
      );

      const actualHash = createHash("sha256")
        .update(responseText.trim())
        .digest("hex");
      const expectedHash = createHash("sha256")
        .update("AGY_ACP_REAL_OK")
        .digest("hex");
      expect(actualHash).toBe(expectedHash);
    } finally {
      connection.close();
      child.stdin.end();
      try {
        const [code, signal] = (await within(exited, "CLI exit", 10_000)) as [
          number | null,
          NodeJS.Signals | null,
        ];
        expect({ code, signal }).toEqual({ code: 0, signal: null });
        expect(stderrBytes).toBe(0);
      } finally {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill("SIGKILL");
        }
      }
    }
  },
  150_000,
);
