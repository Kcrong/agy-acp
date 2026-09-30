import { describe, expect, it } from "vitest";

import { NdjsonParseError, NdjsonParser } from "../../src/ndjson.js";

interface Payload {
  readonly value: string;
}

describe("NdjsonParser", () => {
  it("parses multiple records across arbitrary chunks", () => {
    const parser = new NdjsonParser<Payload>({ maxLineBytes: 128 });

    expect(parser.push('{"value":"first"}\n{"val')).toEqual([
      { value: "first" },
    ]);
    expect(parser.push('ue":"second"}\n')).toEqual([{ value: "second" }]);
    expect(parser.finish()).toEqual([]);
  });

  it("preserves a multibyte UTF-8 character split between byte chunks", () => {
    const parser = new NdjsonParser<Payload>({ maxLineBytes: 128 });
    const input = Buffer.from('{"value":"한글"}\n');
    const splitAt = input.indexOf(Buffer.from("한")) + 1;

    expect(parser.push(input.subarray(0, splitAt))).toEqual([]);
    expect(parser.push(input.subarray(splitAt))).toEqual([{ value: "한글" }]);
  });

  it("accepts CRLF and ignores blank lines", () => {
    const parser = new NdjsonParser<Payload>({ maxLineBytes: 128 });

    expect(parser.push('\r\n  \r\n{"value":"ok"}\r\n')).toEqual([
      { value: "ok" },
    ]);
  });

  it("accepts an exact-size line and rejects an oversized line", () => {
    const line = '{"value":"ok"}';
    const exact = new NdjsonParser<Payload>({
      maxLineBytes: Buffer.byteLength(line),
    });
    const tooSmall = new NdjsonParser<Payload>({
      maxLineBytes: Buffer.byteLength(line) - 1,
    });

    expect(exact.push(`${line}\n`)).toEqual([{ value: "ok" }]);
    expect(() => tooSmall.push(`${line}\n`)).toThrowError(
      expect.objectContaining<Partial<NdjsonParseError>>({
        code: "LINE_TOO_LARGE",
        lineNumber: 1,
      }),
    );
  });

  it("fails closed on malformed JSON and invalid UTF-8", () => {
    const malformed = new NdjsonParser<Payload>({ maxLineBytes: 128 });
    const invalidUtf8 = new NdjsonParser<Payload>({ maxLineBytes: 128 });

    expect(() => malformed.push('{"value":}\n')).toThrowError(
      expect.objectContaining<Partial<NdjsonParseError>>({
        code: "INVALID_JSON",
        lineNumber: 1,
      }),
    );
    expect(() => malformed.push('{"value":"ignored"}\n')).toThrowError(
      expect.objectContaining<Partial<NdjsonParseError>>({
        code: "INVALID_JSON",
      }),
    );
    expect(() => invalidUtf8.push(Buffer.from([0xff, 0x0a]))).toThrowError(
      expect.objectContaining<Partial<NdjsonParseError>>({
        code: "INVALID_UTF8",
        lineNumber: 1,
      }),
    );
  });

  it("parses a final unterminated record and rejects writes after finish", () => {
    const parser = new NdjsonParser<Payload>({ maxLineBytes: 128 });

    expect(parser.push('{"value":"final"}')).toEqual([]);
    expect(parser.finish()).toEqual([{ value: "final" }]);
    expect(() => parser.push("\n")).toThrowError(
      expect.objectContaining<Partial<NdjsonParseError>>({
        code: "PARSER_CLOSED",
      }),
    );
  });
});
