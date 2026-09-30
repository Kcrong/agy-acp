# agy-acp 작업 기록

## 상태

- 현재 단계: ACP agent 구현
- 작업 branch: `feat/agy-acp-bridge`
- draft PR: #2 (`feat(acp): add antigravity ACP bridge`)
- 배포 전제: 향후 public repository와 npm package로 공개하며 다양한 사용자·OS의 clean install을 지원합니다.
- 다음 작업: SDK 1.5.1 fluent `agent()`로 initialize·session/new·prompt·cancel·close handler와 text/resource-link 변환을 RED unit test부터 구현합니다.
- 완료 조건: `roadmap.md`의 모든 checkbox 완료, local gate 통과, 독립 리뷰 완료, PR merge

## 조사 메모

- `--print`는 문자열 인자를 요구합니다. stream mode 시작은 다른 flag 뒤에 `--print=''`를 명시합니다.
- stream input 최상위에는 `event`가 필수이며, 정상 user turn은 `{"event":"user","message":...}` 형식을 사용합니다.
- 알 수 없는 input event는 warning 후 무시되며 process는 성공 종료합니다.
- 정상 출력 순서는 `init` → `step_update` 1개 이상 → `result`입니다.
- `step_update`의 공통 필드는 `conversation_id`, `step_index`, `state`, `step_type`이고 text update에는 `text_delta`, 완료 update에는 `duration_seconds`와 `usage`가 추가될 수 있습니다.
- `init`은 `cwd`, `permission_mode`, `tools`를 포함하고, terminal `result`는 `conversation_id`, `duration_seconds`, `error`, `num_turns`, `response`, `status`, `usage`를 포함합니다.
- 실제 최소 turn은 whitespace 정규화 후 기대 응답과 일치하고 `result.status=SUCCESS`로 완료됐습니다.
- malformed JSON과 `event` 누락은 structured `ERROR` result 후 exit `1`, SIGTERM은 `context canceled` result 후 종료됨을 확인했습니다.
- binary 전체 `strings` 검색은 Go 문자열 테이블이 합쳐져 schema 판별에 사용할 수 없어 폐기했습니다.

## 완료한 작업

### 2026-09-30

- [x] 저장소 작업 규칙을 `AGENTS.md`로 작성하고 PR #1로 merge했습니다.
- [x] 최신 `main`에서 `feat/agy-acp-bridge` branch를 생성했습니다.
- [x] 설치된 `agy` 버전이 `1.2.14`임을 확인했습니다.
- [x] `agy`가 `--input-format stream-json`과 `--output-format stream-json`을 지원함을 확인했습니다.
- [x] 공식 ACP 문서에서 v1이 Latest, v2가 Draft임을 확인했습니다.
- [x] 공식 TypeScript SDK가 `@agentclientprotocol/sdk`이며 신규 구현은 fluent `agent()` API를 사용해야 함을 확인했습니다.
- [x] ACP local agent가 JSON-RPC over stdio를 사용하고 한 연결에서 여러 session 및 bidirectional request를 지원함을 확인했습니다.
- [x] 구현 범위와 완료 게이트를 `roadmap.md`에 작성했습니다.
- [x] roadmap과 tasks를 `docs: define agy acp implementation roadmap`으로 commit하고 push했습니다.
- [x] draft PR #2를 Conventional Commit 형식의 영문 제목과 한국어 4섹션 본문으로 생성했습니다.
- [x] 실제 `agy 1.2.14`의 정상·malformed·unknown event·SIGTERM fixture를 확보하고 `docs/agy-stream-json.md`에 기록했습니다.
- [x] 공식 SDK 1.5.1의 19개 request·8개 notification을 필수·선택·미지원으로 분류해 `docs/acp-v1-contract.md`에 기록했습니다.
- [x] 향후 public repository·npm 배포를 전제로 maintained Node LTS, 다중 OS, clean-install, package 보안 검증 항목을 roadmap에 추가했습니다.
- [x] ACP↔`agy` 변환, session 격리, cancel·timeout·종료, backpressure, typed error 정책을 `docs/bridge-design.md`에 확정했습니다.
- [x] Node `>=22.13.0`, TypeScript `5.9.3`, ACP SDK `1.5.1`, Vitest `5.0.2`, ESLint `10.11.0` 기반 strict ESM scaffold와 exact lockfile을 구성했습니다.
- [x] ESLint type-aware config를 TypeScript 파일로 제한해 자체 JavaScript config parsing 실패를 수정했습니다.
- [x] `npm run check`: lint, typecheck, unit 2/2, E2E command, build 통과.
- [x] bounded NDJSON parser를 RED test 후 구현하고 arbitrary chunk, multibyte UTF-8, CRLF, blank line, exact/oversized line, malformed JSON, invalid UTF-8, final record, closed state unit 6/6을 통과했습니다.
- [x] `agy` init·step_update·result를 normalized typed union으로 검증하고 unknown event를 forward-compatible하게 보존하는 parser를 RED test 후 구현했습니다.
- [x] Event parser targeted 10/10, 전체 unit 18/18, typecheck, lint, build 통과.
- [x] `agy` invocation builder와 spawn boundary를 RED test 후 구현했습니다. Attached argv, absolute path, literal injection-like 값, conversation/add-dir, `shell:false`, piped stdio, caller env 상속을 targeted 7/7로 검증했습니다.
- [x] ProcessController의 split init, typed stdout dispatch, single fatal failure, init timeout, pre-init exit, bounded stderr metadata, drain backpressure를 RED test 후 구현했습니다.
- [x] ProcessController targeted 7/7, 전체 unit 32/32, typecheck, lint, build 통과.
- [x] ProcessController에 single active turn, terminal result, busy rejection, prompt timeout, cancel, graceful close, SIGTERM→SIGKILL escalation, idempotent shutdown, listener·timer cleanup을 RED test 후 구현했습니다.
- [x] Expanded ProcessController targeted 12/12, typecheck, lint 통과.
- [x] SessionManager에 multi-session controller 격리, opaque ID create/load 검증, duplicate/mismatch 방어, cancel·failure 후 coalesced lazy restart, close/closeAll을 RED test 후 구현했습니다.
- [x] SessionManager targeted 7/7, 전체 unit 44/44, typecheck, lint, build 통과.

## 진행 중

- [x] `agy` stream-json protocol fixture 수집
- [x] ACP v1 method·capability 목록 확정
- [x] ACP↔`agy` 변환 계약 작성
- [x] TypeScript strict ESM project scaffold
- [x] bounded NDJSON parser
- [x] typed agy event parser
- [x] shell 없는 argv/spawn boundary
- [x] ProcessController init·stream·backpressure
- [x] ProcessController cancel·timeout·cleanup
- [x] SessionManager isolation·lifecycle
- [ ] ACP AgentApp baseline handlers

## 대기

- [ ] GitHub Actions 실행: 사용량이 복구될 때까지 실행 전 quota 실패는 허용하되 workflow는 작성합니다.

## 반복 운영 규칙

각 monitoring cycle에서 다음 순서로 진행합니다.

1. `roadmap.md`, `tasks.md`, Git 상태를 읽습니다.
2. 완료되지 않은 가장 작은 검증 가능한 작업 하나를 선택합니다.
3. 구현 또는 테스트를 수행합니다.
4. 관련 local 검증을 실행합니다.
5. 증거가 확인된 항목만 `roadmap.md`에서 완료 처리하고 `tasks.md`에 결과와 다음 작업을 기록합니다.
6. roadmap이 남아 있으면 다음 cycle을 계속하고, 모두 완료되면 최종 리뷰·PR·merge를 수행한 뒤 loop를 종료합니다.
7. 자격 증명, 토큰, 인증 파일 내용은 읽거나 출력하지 않습니다.
