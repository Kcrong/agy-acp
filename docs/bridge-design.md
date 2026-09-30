# agy-acp bridge 설계

기준은 ACP v1 및 `@agentclientprotocol/sdk 1.5.1`, 실제 `agy 1.2.14` stream-json 관찰 결과입니다.

## 구성 요소

```text
ACP stdio
  → AgentApp handlers
  → SessionManager
  → AgySession / ProcessController
  → agy stream-json NDJSON
```

- `AgentApp`는 ACP schema validation과 JSON-RPC framing을 담당합니다.
- `SessionManager`는 한 ACP connection 안의 여러 session을 격리합니다.
- `AgySession`은 session별 prompt 직렬화와 `agy` conversation ID를 관리합니다.
- `ProcessController`는 shell 없이 child process, NDJSON, backpressure, signal을 관리합니다.

## ACP → agy 변환

| ACP 입력 | 검증 | agy 동작 |
|---|---|---|
| `initialize` | 지원 protocol version | process를 시작하지 않고 구현 완료된 capability만 반환합니다. |
| `session/new` | `cwd`와 추가 directory가 absolute path, `mcpServers`가 비어 있음 | `cwd`에서 `agy --add-dir <path>... --input-format stream-json --output-format stream-json --print=''`를 실행하고 init을 기다립니다. |
| `session/load` | opaque session ID, absolute path | 동일 argv에 `--conversation <id>`를 추가하고 init을 기다립니다. |
| `session/prompt` Text | 비어 있지 않은 text | ACP content를 하나의 user message로 합쳐 `event:user` NDJSON 한 줄을 씁니다. |
| `session/prompt` ResourceLink | name·URI 존재 | `Resource: <name> (<uri>)` text reference로 변환합니다. |
| `session/cancel` | 존재하며 실행 중인 session | 현재 process에 graceful termination을 요청합니다. |
| `session/close` | 존재하는 session | stdin을 닫고 process와 listener를 정리한 뒤 map에서 제거합니다. |

`mcpServers`가 비어 있지 않으면 global `agy` 설정을 변경하지 않고 `Invalid params`로 거부합니다. client가 제공한 MCP server를 process-scoped로 안전하게 주입할 공개 CLI가 확인되기 전에는 MCP를 지원한다고 광고하지 않습니다.

Prompt block은 원래 순서를 유지하며 빈 text는 제거하고 block 사이를 두 개의 newline으로 구분합니다. Image, Audio, Resource는 capability를 광고하지 않으며 들어오면 `Invalid params`로 거부합니다.

## agy → ACP 변환

| agy event | ACP 출력 |
|---|---|
| `init` | session의 opaque conversation ID를 확정하며 update는 보내지 않습니다. |
| `step_update.text_delta` | 동일 message ID의 `session/update` + `agent_message_chunk` Text content |
| `step_update` without text | 내부 lifecycle만 갱신하고 debug 진단 외에는 전송하지 않습니다. |
| `result.status=SUCCESS` | `PromptResponse { stopReason: "end_turn" }` |
| cancel 이후 `result.status=ERROR` + context cancellation | `PromptResponse { stopReason: "cancelled" }` |
| 기타 `result.status=ERROR` | typed execution error를 JSON-RPC error로 변환합니다. |
| malformed NDJSON·조기 EOF·non-zero exit | typed transport/process error를 JSON-RPC error로 변환합니다. |
| unknown event | bounded stderr 진단 후 무시합니다. |

Result의 response가 이미 `text_delta`로 모두 전송된 경우 중복 전송하지 않습니다. delta가 전혀 없지만 result response가 있으면 final text를 한 번 전송합니다. Usage는 안정 계약에서 제외하고 raw token 수치를 protocol이나 기본 로그에 노출하지 않습니다.

## Session identity와 process lifecycle

1. `session/new`는 process의 첫 `init`을 제한 시간 안에 기다립니다.
2. ACP session ID는 `agy`의 opaque conversation ID를 사용합니다. 값은 비교·argv 전달 외에 해석하지 않습니다.
3. session별로 최대 한 prompt만 실행합니다. 같은 session의 중첩 prompt는 busy error로 거부합니다.
4. 서로 다른 session은 각각 독립 process를 사용해 병렬 실행할 수 있습니다.
5. idle process가 종료되면 session metadata는 유지하고 다음 prompt에서 `--conversation`으로 한 번 lazy restart합니다.
6. ACP connection 종료 시 모든 child stdin을 닫고 bounded shutdown을 수행합니다.
7. close된 session의 후속 prompt와 중복 close는 명확한 unknown/closed-session error를 반환합니다.

## 취소와 timeout

- init timeout 기본값: 15초
- prompt timeout 기본값: 30분, public CLI option으로 조정 가능
- graceful cancel period 기본값: 5초
- hard-kill 대기 기본값: 2초

취소 순서:

1. session의 active prompt를 cancelled 상태로 원자적으로 전환합니다.
2. POSIX에서는 SIGTERM, Windows에서는 Node child termination abstraction을 사용합니다.
3. grace period 안에 structured result 또는 exit를 기다립니다.
4. 남아 있으면 platform-specific hard kill을 수행합니다.
5. prompt를 `cancelled`로 종료하고 다음 prompt에서 conversation을 lazy restart할 수 있게 합니다.

Prompt timeout도 같은 종료 절차를 사용하지만 `cancelled`가 아니라 timeout execution error로 반환합니다. Client cancel과 timeout이 경쟁하면 최초 terminal transition만 유효합니다.

## Backpressure와 bounded resource

- stdin write가 `false`를 반환하면 `drain` 후 다음 message를 씁니다.
- stdout event listener는 async completion을 반환할 수 있으며, downstream ACP notification이 끝날 때까지 child stdout을 pause합니다.
- Event batch는 순서대로 하나씩 처리하고 최신 batch가 성공한 뒤에만 stdout을 resume합니다. Listener rejection은 즉시 typed failure와 bounded shutdown으로 전환합니다.
- stdout은 chunk 경계를 신뢰하지 않고 newline 기준 bounded parser로 처리합니다.
- 최대 line bytes, stderr ring bytes, init/prompt/cancel timeout은 중앙 configuration에서 제한합니다.
- parser error 이후 해당 process의 추가 output은 신뢰하지 않고 종료합니다.
- Child `exit`는 상태만 기록하고 stdout EOF와 async event queue drain 뒤 `close`를 terminal barrier로 사용합니다. Result 없는 stdout EOF는 즉시 typed failure입니다.
- Known init/update/result는 `initializing → idle → active → idle` phase에서만 허용하며 idle result와 contradictory status/error를 거부합니다.
- stdin write/drain failure는 controller 전체를 retire해 다음 prompt가 새 process를 기다리도록 합니다.
- listener와 timer는 모든 success·error·cancel·disconnect 경로에서 한 번만 정리합니다.

## 오류와 capability 원칙

- initialize는 구현 및 E2E가 완료된 capability만 광고합니다.
- 등록하지 않은 ACP method는 SDK의 JSON-RPC `Method not found`를 사용합니다.
- 지원 method의 잘못된 path, prompt block, non-empty MCP list는 `Invalid params`로 반환합니다.
- 실행 파일 없음, init timeout, malformed output, 조기 exit, busy session, unknown session, prompt timeout을 서로 다른 typed error code로 구분합니다.
- 최대 active+starting session 수를 admission 전에 원자적으로 reserve하며 기본값은 16입니다. 초과 요청은 process를 만들기 전에 거부합니다.
- SDK `$/cancel_request` AbortSignal은 동일 session cancel에 연결하고 `RequestError.requestCancelled`로 응답합니다.
- Client path/config 오류는 `Invalid params`, backend identity/manager 오류는 `Internal error`, capacity 초과는 bounded server error로 구분합니다.
- stderr 원문, command environment, credential, conversation ID는 기본 오류 메시지에 포함하지 않습니다.
- `agy`는 argv 배열과 `shell: false`로 실행하고 caller environment는 상속하되 열거하거나 로그로 출력하지 않습니다.

## 완료 검증

이 계약은 unit test와 fake-`agy` E2E의 test matrix로 직접 변환합니다. 선택 capability인 load, close, additional directories는 각각 성공·거부·경계 E2E가 통과한 뒤에만 initialize response에 활성화합니다.
