#!/usr/bin/env node

import { createInterface } from "node:readline";

const conversationArgument = process.argv.find((argument) =>
  argument.startsWith("--conversation="),
);
const conversationId =
  conversationArgument?.slice("--conversation=".length) ??
  `fake-session-${String(process.pid)}`;

function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

emit({
  event: "init",
  conversation_id: conversationId,
  init: {
    cwd: process.cwd(),
    permission_mode: "default",
    tools: [],
  },
});

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  const input = JSON.parse(line);
  if (input.event !== "user") {
    continue;
  }

  emit({
    event: "step_update",
    step_update: {
      conversation_id: conversationId,
      step_index: 0,
      state: "running",
      step_type: "assistant_text",
      text_delta: "fake-response",
    },
  });
  emit({
    event: "result",
    result: {
      conversation_id: conversationId,
      duration_seconds: 0,
      error: null,
      num_turns: 1,
      response: "fake-response",
      status: "SUCCESS",
      usage: {},
    },
  });
}
