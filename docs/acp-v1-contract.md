# ACP v1 지원 계약

이 문서는 공식 `@agentclientprotocol/sdk 1.5.1` TypeScript reference와 ACP v1 Latest 문서를 기준으로 합니다. v2 Draft 및 SDK의 experimental 기능은 범위에서 제외합니다.

## Baseline 필수 기능

ACP v1 agent는 다음 기능을 반드시 지원합니다.

| 방향 | Method | agy-acp 책임 |
|---|---|---|
| client → agent request | `initialize` | protocol version을 협상하고 실제 capability만 반환합니다. |
| client → agent request | `session/new` | ACP session을 만들고 독립된 `agy` process를 준비합니다. |
| client → agent request | `session/prompt` | prompt를 `agy` user event로 보내고 terminal result까지 관리합니다. |
| client → agent notification | `session/cancel` | 현재 turn에 SIGTERM을 보내고 grace period 이후 강제 종료합니다. |
| agent → client notification | `session/update` | `agy.step_update.text_delta`를 streaming content update로 전달합니다. |

`session/update`는 agent가 client로 보내는 notification이므로 `AgentApp` inbound handler가 아니라 request handler의 `AgentContext`를 통해 전송합니다.

## Prompt content capability

ACP baseline인 `ContentBlock::Text`와 `ContentBlock::ResourceLink`를 지원합니다.

- Text는 `agy` user message text로 전달합니다.
- ResourceLink는 표시 이름과 URI를 명시적인 text reference로 직렬화합니다.
- `image`, `audio`, `embeddedContext`는 검증 전까지 광고하지 않습니다.

초기 capability는 `promptCapabilities: {}`로 baseline content만 나타냅니다.

## 구현할 선택 기능

| Method/capability | 근거 | 광고 조건 |
|---|---|---|
| `session/load` / `loadSession: true` | `agy --conversation <id>`가 기존 conversation 재개를 지원합니다. | 실제 load E2E 통과 후 활성화합니다. |
| `session/close` / `sessionCapabilities.close: {}` | child process와 listener를 명시적으로 정리할 수 있습니다. | close·중복 close E2E 통과 후 활성화합니다. |
| `sessionCapabilities.additionalDirectories: {}` | `agy --add-dir`가 반복 가능한 추가 directory를 지원합니다. | path validation 및 process argv E2E 통과 후 활성화합니다. |

선택 capability는 구현과 테스트가 완료되기 전에는 initialize response에 포함하지 않습니다.

## 광고하지 않는 기능

다음 기능은 현재 `agy` stream-json interface로 신뢰성 있게 제공할 근거가 없어 handler와 capability를 등록하지 않습니다.

### Session request

- `session/fork`
- `session/list`
- `session/delete`
- `session/resume`
- `session/set_mode`
- `session/set_config_option`

### Authentication·provider

- `authenticate`
- `providers/list`
- `providers/set`
- `providers/disable`
- `logout`

인증과 provider 선택은 설치된 `agy`가 소유합니다. Adapter는 credential 파일을 읽거나 별도 인증 상태를 저장하지 않습니다.

### Document·NES notification/request

- `document/didOpen`, `document/didChange`, `document/didClose`, `document/didSave`, `document/didFocus`
- `nes/start`, `nes/suggest`, `nes/close`, `nes/accept`, `nes/reject`

### 기타 capability

- prompt `image`, `audio`, `embeddedContext`
- MCP `http`, `sse`, experimental `acp`
- experimental `providers`, `nes`, `positionEncoding`, `session/fork`

등록하지 않은 method는 SDK connection layer의 표준 JSON-RPC `Method not found` 오류로 응답합니다. 지원 method의 잘못된 params와 지원하지 않는 content type은 표준 `Invalid params` 계열 오류로 반환합니다.

## 전체 SDK inbound 표면

SDK 1.5.1의 agent request는 19개, agent notification은 8개입니다.

- Requests: `initialize`, `session/new`, `session/load`, `session/fork`, `session/list`, `session/delete`, `session/resume`, `session/close`, `session/set_mode`, `session/set_config_option`, `authenticate`, `providers/list`, `providers/set`, `providers/disable`, `logout`, `session/prompt`, `nes/start`, `nes/suggest`, `nes/close`
- Notifications: `session/cancel`, `document/didOpen`, `document/didChange`, `document/didClose`, `document/didSave`, `document/didFocus`, `nes/accept`, `nes/reject`

지원 여부는 이 목록의 존재가 아니라 initialize에서 광고한 capability와 등록한 handler로 결정합니다.
