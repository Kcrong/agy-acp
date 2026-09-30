import { describe, expect, it } from "vitest";

import {
  AgyEventValidationError,
  parseAgyEvent,
} from "../../src/agy-events.js";

describe("parseAgyEvent", () => {
  it("normalizes an init event", () => {
    expect(
      parseAgyEvent({
        event: "init",
        conversation_id: "opaque-id",
        init: {
          cwd: "/workspace",
          permission_mode: "default",
          tools: [{ name: "tool" }],
        },
      }),
    ).toEqual({
      kind: "init",
      conversationId: "opaque-id",
      cwd: "/workspace",
      permissionMode: "default",
      tools: [{ name: "tool" }],
    });
  });

  it("normalizes text and completion step updates", () => {
    expect(
      parseAgyEvent({
        event: "step_update",
        step_update: {
          conversation_id: "opaque-id",
          step_index: 2,
          state: "running",
          step_type: "assistant_text",
          text_delta: "hello",
          duration_seconds: 0.5,
          usage: { total_tokens: 10 },
        },
      }),
    ).toEqual({
      kind: "step_update",
      conversationId: "opaque-id",
      stepIndex: 2,
      state: "running",
      stepType: "assistant_text",
      textDelta: "hello",
      durationSeconds: 0.5,
      usage: { total_tokens: 10 },
    });
  });

  it.each(["SUCCESS", "ERROR"] as const)(
    "normalizes a %s result event",
    (status) => {
      expect(
        parseAgyEvent({
          event: "result",
          result: {
            conversation_id: "opaque-id",
            duration_seconds: 1,
            ...(status === "ERROR" ? { error: "failed" } : {}),
            num_turns: 1,
            response: "result text",
            status,
            usage: {},
          },
        }),
      ).toEqual({
        kind: "result",
        conversationId: "opaque-id",
        durationSeconds: 1,
        error: status === "ERROR" ? "failed" : null,
        numTurns: 1,
        response: "result text",
        status,
        usage: {},
      });
    },
  );

  it("preserves an unknown event without trusting its shape", () => {
    const raw = { event: "future_event", payload: { value: 1 } };

    expect(parseAgyEvent(raw)).toEqual({
      kind: "unknown",
      name: "future_event",
      raw,
    });
  });

  it.each([
    [{}, "event"],
    [{ event: "init", init: {} }, "conversation_id"],
    [
      {
        event: "step_update",
        step_update: {
          conversation_id: "id",
          step_index: -1,
          state: "running",
          step_type: "text",
        },
      },
      "step_index",
    ],
    [
      {
        event: "result",
        result: {
          conversation_id: "id",
          duration_seconds: 1,
          error: null,
          num_turns: 1,
          response: "text",
          status: "MAYBE",
          usage: {},
        },
      },
      "status",
    ],
  ] as const)("rejects an invalid known event at %s", (input, field) => {
    expect(() => parseAgyEvent(input)).toThrowError(
      expect.objectContaining<Partial<AgyEventValidationError>>({
        code: "INVALID_AGY_EVENT",
        field,
      }),
    );
  });

  it("never includes an invalid field value in its error message", () => {
    const sensitiveValue = "do-not-echo-this-value";
    let thrown: unknown;

    try {
      parseAgyEvent({ event: "init", conversation_id: sensitiveValue, init: {} });
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(AgyEventValidationError);
    if (!(thrown instanceof Error)) {
      throw new TypeError("expected parseAgyEvent to throw an Error");
    }
    expect(thrown.message).not.toContain(sensitiveValue);
  });
});

describe("parseAgyEvent result invariants", () => {
  it.each([
    ["SUCCESS", "unexpected error"],
    ["ERROR", null],
  ] as const)("rejects contradictory %s/error state", (status, error) => {
    expect(() =>
      parseAgyEvent({
        event: "result",
        result: {
          conversation_id: "opaque-id",
          duration_seconds: 1,
          error,
          num_turns: 1,
          response: "text",
          status,
          usage: {},
        },
      }),
    ).toThrowError(
      expect.objectContaining<Partial<AgyEventValidationError>>({
        code: "INVALID_AGY_EVENT",
        field: "error",
      }),
    );
  });
});
