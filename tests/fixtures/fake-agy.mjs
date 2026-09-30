#!/usr/bin/env node

import { createInterface } from "node:readline";
import { setImmediate as immediate } from "node:timers/promises";

const mode = process.env.FAKE_AGY_MODE ?? "normal";
const conversationArgument = process.argv.find((argument) =>
  argument.startsWith("--conversation="),
);
const conversationId =
  conversationArgument?.slice("--conversation=".length) ??
  `fake-session-${String(process.pid)}`;
let active = false;

async function emit(value) {
  const line = `${JSON.stringify(value)}\n`;
  if (mode !== "split") {
    process.stdout.write(line);
    return;
  }

  const middle = Math.max(1, Math.floor(line.length / 2));
  process.stdout.write(line.slice(0, middle));
  await immediate();
  process.stdout.write(line.slice(middle));
}

function result(status, error = null) {
  return {
    event: "result",
    result: {
      conversation_id: conversationId,
      duration_seconds: 0,
      error,
      num_turns: 1,
      response: status === "SUCCESS" ? "fake-response" : "",
      status,
      usage: {},
    },
  };
}

await emit({
  event: "init",
  conversation_id: conversationId,
  init: {
    cwd: process.cwd(),
    permission_mode: "default",
    tools: [],
  },
});

process.on("SIGTERM", () => {
  if (!active) {
    process.exit(0);
  }
  process.stdout.write(
    `${JSON.stringify(result("ERROR", "context canceled"))}\n`,
    () => process.exit(0),
  );
});

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  const input = JSON.parse(line);
  if (input.event !== "user") {
    continue;
  }

  active = true;
  if (mode === "malformed") {
    process.stdout.write("{broken}\n");
    continue;
  }
  if (mode === "early-exit") {
    process.exit(7);
  }

  await emit({
    event: "step_update",
    step_update: {
      conversation_id: conversationId,
      step_index: 0,
      state: "running",
      step_type: "assistant_text",
      text_delta: mode === "hang" ? "started" : "fake-response",
    },
  });

  if (mode === "hang") {
    continue;
  }

  await emit(result("SUCCESS"));
  active = false;
}
