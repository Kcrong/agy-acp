# agy stream-json 관찰 계약

이 문서는 Linux x86-64 환경의 `agy 1.2.14`에서 직접 관찰한 비민감 protocol fixture입니다. 자격 증명, conversation ID, 응답 본문, token 수치는 기록하지 않습니다.

## 실행 형식

```bash
agy \
  --input-format stream-json \
  --output-format stream-json \
  --print-timeout 90s \
  --print=''
```

`--print`는 문자열 인자를 요구하므로 stream mode에서도 빈 문자열을 명시해야 합니다. 입력과 출력은 한 줄에 JSON 객체 하나인 NDJSON입니다.

## User input

```json
{"event":"user","message":{"role":"user","content":[{"type":"text","text":"<prompt>"}]}}
```

최상위 `event`가 없으면 terminal error가 발생합니다. 알 수 없는 event는 warning 후 무시됩니다.

## 정상 출력

관찰된 순서는 다음과 같습니다.

```text
init → step_update 1개 이상 → result
```

### init

```json
{
  "event": "init",
  "conversation_id": "<opaque>",
  "init": {
    "cwd": "<path>",
    "permission_mode": "<mode>",
    "tools": []
  }
}
```

`conversation_id`는 opaque 값으로 취급하고 로그에 출력하지 않습니다.

### step_update

모든 update에 다음 필드가 관찰됐습니다.

```json
{
  "event": "step_update",
  "step_update": {
    "conversation_id": "<opaque>",
    "step_index": 0,
    "state": "<state>",
    "step_type": "<type>"
  }
}
```

text streaming update에는 `text_delta: string`이 추가됩니다. 완료 update에는 `duration_seconds: number`와 `usage: object`가 추가될 수 있습니다. `usage`의 key는 `cache_read_tokens`, `input_tokens`, `output_tokens`, `thinking_tokens`, `total_tokens`입니다.

### result

```json
{
  "event": "result",
  "result": {
    "conversation_id": "<opaque>",
    "duration_seconds": 0,
    "num_turns": 1,
    "response": "<final text>",
    "status": "SUCCESS",
    "usage": {}
  }
}
```

실제 최소 turn에서 whitespace를 제거한 `response`가 요청한 고정 문자열과 일치했고 `status=SUCCESS`였습니다. `SUCCESS` result에서는 `error` key가 생략될 수 있으며 adapter는 이를 `null`로 normalize합니다. `ERROR` result의 `error`는 문자열입니다.

## 실패·취소 fixture

| 흐름 | 관찰된 event | stderr | process 결과 |
|---|---|---|---|
| malformed JSON | `init`, `result(status=ERROR)` | decode error | exit `1` |
| 최상위 `event` 누락 | `init`, `result(status=ERROR)` | missing event | exit `1` |
| 알 수 없는 event | `init` | ignored-event warning | exit `0` |
| 진행 중 SIGTERM | `result(status=ERROR)` | `context canceled` | `timeout` wrapper exit `124` |

SIGTERM 흐름에서 `agy`는 종료 전에 structured error result를 출력했습니다. 구현에서는 ACP cancel 시 SIGTERM을 먼저 보내고 grace period 이후에만 강제 종료합니다.

## Adapter 해석 원칙

- `text_delta`만 ACP streaming text update로 전달합니다.
- terminal `result.status=SUCCESS`를 정상 완료 조건으로 사용합니다.
- `ERROR`, malformed NDJSON, 조기 EOF, non-zero exit를 typed bridge error로 변환합니다.
- unknown event는 forward compatibility를 위해 조용히 무시하며 raw event 이름이나 payload를 기본 로그에 남기지 않습니다.
- `conversation_id`, usage, stderr는 ACP protocol `stdout`에 임의로 출력하지 않습니다.
