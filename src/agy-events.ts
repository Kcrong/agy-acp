type JsonRecord = Record<string, unknown>;

export type AgyResultStatus = "SUCCESS" | "ERROR";

export interface AgyInitEvent {
  readonly kind: "init";
  readonly conversationId: string;
  readonly cwd: string;
  readonly permissionMode: string;
  readonly tools: readonly unknown[];
}

export interface AgyStepUpdateEvent {
  readonly kind: "step_update";
  readonly conversationId: string;
  readonly stepIndex: number;
  readonly state: string;
  readonly stepType: string;
  readonly textDelta: string | undefined;
  readonly durationSeconds: number | undefined;
  readonly usage: Readonly<JsonRecord> | undefined;
}

export interface AgyResultEvent {
  readonly kind: "result";
  readonly conversationId: string;
  readonly durationSeconds: number;
  readonly error: string | null;
  readonly numTurns: number;
  readonly response: string;
  readonly status: AgyResultStatus;
  readonly usage: Readonly<JsonRecord>;
}

export interface AgyUnknownEvent {
  readonly kind: "unknown";
  readonly name: string;
  readonly raw: Readonly<JsonRecord>;
}

export type AgyEvent =
  | AgyInitEvent
  | AgyStepUpdateEvent
  | AgyResultEvent
  | AgyUnknownEvent;

export class AgyEventValidationError extends Error {
  public readonly code = "INVALID_AGY_EVENT";
  public readonly field: string;
  public readonly eventName: string | undefined;

  public constructor(field: string, eventName?: string) {
    super(`Invalid agy event field: ${field}`);
    this.name = "AgyEventValidationError";
    this.field = field;
    this.eventName = eventName;
  }
}

export function parseAgyEvent(value: unknown): AgyEvent {
  const envelope = requireRecord(value, "event");
  const eventName = requireString(envelope, "event");

  switch (eventName) {
    case "init":
      return parseInit(envelope);
    case "step_update":
      return parseStepUpdate(envelope);
    case "result":
      return parseResult(envelope);
    default:
      return { kind: "unknown", name: eventName, raw: envelope };
  }
}

function parseInit(envelope: JsonRecord): AgyInitEvent {
  const eventName = "init";
  const conversationId = requireString(
    envelope,
    "conversation_id",
    eventName,
  );
  const init = requireRecord(envelope.init, "init", eventName);
  const tools = init.tools;
  if (!Array.isArray(tools)) {
    fail("tools", eventName);
  }

  return {
    kind: "init",
    conversationId,
    cwd: requireString(init, "cwd", eventName),
    permissionMode: requireString(init, "permission_mode", eventName),
    tools,
  };
}

function parseStepUpdate(envelope: JsonRecord): AgyStepUpdateEvent {
  const eventName = "step_update";
  const update = requireRecord(envelope.step_update, "step_update", eventName);

  return {
    kind: "step_update",
    conversationId: requireString(update, "conversation_id", eventName),
    stepIndex: requireNonNegativeInteger(update, "step_index", eventName),
    state: requireString(update, "state", eventName),
    stepType: requireString(update, "step_type", eventName),
    textDelta: optionalString(update, "text_delta", eventName),
    durationSeconds: optionalNonNegativeNumber(
      update,
      "duration_seconds",
      eventName,
    ),
    usage: optionalRecord(update, "usage", eventName),
  };
}

function parseResult(envelope: JsonRecord): AgyResultEvent {
  const eventName = "result";
  const result = requireRecord(envelope.result, "result", eventName);
  const status = result.status;
  const error = result.error;

  if (status !== "SUCCESS" && status !== "ERROR") {
    fail("status", eventName);
  }
  if (error !== null && typeof error !== "string") {
    fail("error", eventName);
  }

  return {
    kind: "result",
    conversationId: requireString(result, "conversation_id", eventName),
    durationSeconds: requireNonNegativeNumber(
      result,
      "duration_seconds",
      eventName,
    ),
    error,
    numTurns: requireNonNegativeInteger(result, "num_turns", eventName),
    response: requireString(result, "response", eventName),
    status,
    usage: requireRecord(result.usage, "usage", eventName),
  };
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(
  value: unknown,
  field: string,
  eventName?: string,
): JsonRecord {
  if (!isRecord(value)) {
    fail(field, eventName);
  }
  return value;
}

function optionalRecord(
  record: JsonRecord,
  field: string,
  eventName: string,
): JsonRecord | undefined {
  const value = record[field];
  if (value === undefined) {
    return undefined;
  }
  return requireRecord(value, field, eventName);
}

function requireString(
  record: JsonRecord,
  field: string,
  eventName?: string,
): string {
  const value = record[field];
  if (typeof value !== "string" || value.length === 0) {
    fail(field, eventName);
  }
  return value;
}

function optionalString(
  record: JsonRecord,
  field: string,
  eventName: string,
): string | undefined {
  const value = record[field];
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== "string") {
    fail(field, eventName);
  }
  return value;
}

function requireNonNegativeInteger(
  record: JsonRecord,
  field: string,
  eventName: string,
): number {
  const value = record[field];
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    fail(field, eventName);
  }
  return value as number;
}

function requireNonNegativeNumber(
  record: JsonRecord,
  field: string,
  eventName: string,
): number {
  const value = record[field];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    fail(field, eventName);
  }
  return value;
}

function optionalNonNegativeNumber(
  record: JsonRecord,
  field: string,
  eventName: string,
): number | undefined {
  if (record[field] === undefined) {
    return undefined;
  }
  return requireNonNegativeNumber(record, field, eventName);
}

function fail(field: string, eventName?: string): never {
  throw new AgyEventValidationError(field, eventName);
}
