const SKIP_LINE = Symbol("skip-line");
const UTF8_DECODER = new TextDecoder("utf-8", { fatal: true });

export type NdjsonErrorCode =
  | "INVALID_LIMIT"
  | "LINE_TOO_LARGE"
  | "INVALID_UTF8"
  | "INVALID_JSON"
  | "PARSER_CLOSED";

export interface NdjsonParserOptions {
  readonly maxLineBytes: number;
}

export class NdjsonParseError extends Error {
  public readonly code: NdjsonErrorCode;
  public readonly lineNumber: number;

  public constructor(
    code: NdjsonErrorCode,
    lineNumber: number,
    message: string,
    cause?: unknown,
  ) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "NdjsonParseError";
    this.code = code;
    this.lineNumber = lineNumber;
  }
}

export class NdjsonParser<T = unknown> {
  readonly #maxLineBytes: number;
  #segments: Buffer[] = [];
  #pendingBytes = 0;
  #lineNumber = 1;
  #closed = false;
  #failure: NdjsonParseError | undefined;

  public constructor(options: NdjsonParserOptions) {
    if (
      !Number.isSafeInteger(options.maxLineBytes) ||
      options.maxLineBytes <= 0
    ) {
      throw new NdjsonParseError(
        "INVALID_LIMIT",
        1,
        "maxLineBytes must be a positive safe integer",
      );
    }

    this.#maxLineBytes = options.maxLineBytes;
  }

  public push(chunk: string | Uint8Array): T[] {
    this.#assertWritable();

    const bytes =
      typeof chunk === "string" ? Buffer.from(chunk, "utf8") : Buffer.from(chunk);
    const values: T[] = [];
    let segmentStart = 0;

    for (let index = 0; index < bytes.length; index += 1) {
      if (bytes[index] !== 0x0a) {
        continue;
      }

      this.#append(bytes.subarray(segmentStart, index));
      const value = this.#parsePendingLine();
      if (value !== SKIP_LINE) {
        values.push(value);
      }
      segmentStart = index + 1;
    }

    this.#append(bytes.subarray(segmentStart));
    return values;
  }

  public finish(): T[] {
    this.#assertWritable();
    this.#closed = true;

    if (this.#pendingBytes === 0) {
      return [];
    }

    const value = this.#parsePendingLine();
    return value === SKIP_LINE ? [] : [value];
  }

  #append(segment: Uint8Array): void {
    if (segment.byteLength === 0) {
      return;
    }

    const nextSize = this.#pendingBytes + segment.byteLength;
    if (nextSize > this.#maxLineBytes) {
      this.#fail(
        "LINE_TOO_LARGE",
        `NDJSON line ${this.#lineNumber} exceeds ${this.#maxLineBytes} bytes`,
      );
    }

    this.#segments.push(Buffer.from(segment));
    this.#pendingBytes = nextSize;
  }

  #parsePendingLine(): T | typeof SKIP_LINE {
    const currentLine = this.#lineNumber;
    const joined = Buffer.concat(this.#segments, this.#pendingBytes);
    const line =
      joined.at(-1) === 0x0d ? joined.subarray(0, joined.length - 1) : joined;

    this.#segments = [];
    this.#pendingBytes = 0;
    this.#lineNumber += 1;

    let text: string;
    try {
      text = UTF8_DECODER.decode(line);
    } catch (error) {
      this.#fail(
        "INVALID_UTF8",
        `NDJSON line ${currentLine} is not valid UTF-8`,
        currentLine,
        error,
      );
    }

    if (text.trim().length === 0) {
      return SKIP_LINE;
    }

    try {
      return JSON.parse(text) as T;
    } catch (error) {
      this.#fail(
        "INVALID_JSON",
        `NDJSON line ${currentLine} is not valid JSON`,
        currentLine,
        error,
      );
    }
  }

  #assertWritable(): void {
    if (this.#failure !== undefined) {
      throw this.#failure;
    }
    if (this.#closed) {
      throw new NdjsonParseError(
        "PARSER_CLOSED",
        this.#lineNumber,
        "NDJSON parser is already closed",
      );
    }
  }

  #fail(
    code: NdjsonErrorCode,
    message: string,
    lineNumber = this.#lineNumber,
    cause?: unknown,
  ): never {
    const error = new NdjsonParseError(code, lineNumber, message, cause);
    this.#failure = error;
    throw error;
  }
}
