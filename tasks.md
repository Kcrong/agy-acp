# agy-acp 작업 기록

## 상태

- 현재 단계: focused public-review Medium 2개 수정 완료·successor 재리뷰 준비
- 작업 branch: `feat/agy-acp-bridge`
- draft PR: #2 (`feat(acp): add antigravity ACP bridge`)
- 배포 전제: 향후 public repository와 npm package로 공개하며 다양한 사용자·OS의 clean install을 지원합니다.
- 다음 작업: rejection-path cancellation과 Windows package environment 수정 commit을 push하고 successor exact head에서 두 finding closure를 확인합니다.
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
- [x] SDK `agent()` fluent API로 initialize, new/load/prompt/cancel/close와 disconnect cleanup을 구현했습니다. E2E 전에는 baseline prompt capability만 광고합니다.
- [x] Text·ResourceLink를 `agy` user event로 변환하고 `text_delta`를 ACP `agent_message_chunk`로 순서대로 streaming합니다.
- [x] SDK runtime peer `zod 4.6.5`를 public consumer 재현성을 위해 direct exact dependency로 고정했습니다.
- [x] ACP Agent targeted 4/4, 전체 unit 48/48, typecheck, lint, build 통과.
- [x] Node stdin/stdout을 SDK `ndJsonStream`에 연결하고 `--agy-path`, signal/EOF cleanup, fixed stderr failure를 제공하는 real CLI를 구현했습니다.
- [x] Credential-free fake `agy` executable과 실제 ACP client→CLI→child→streaming→close E2E harness를 구현했습니다.
- [x] SDK connection close만으로 Node pipe EOF가 발생하지 않는 hang을 단계별 3초 timeout으로 특정하고 client stdin EOF를 명시해 수정했습니다.
- [x] `npm run check`: unit 48/48, normal stdio E2E 1/1, lint, typecheck, build 통과.
- [x] Runtime limits를 양의 정수 환경변수로 override하고 잘못된 값을 echo하지 않는 parser를 unit test와 함께 구현했습니다.
- [x] fake `agy`의 split·malformed·early-exit·hang/SIGTERM modes와 단계별 3초 fail-fast E2E cleanup을 구현했습니다.
- [x] Split streaming, malformed JSON, 조기 exit, prompt timeout, cancellation, 두 session 동시 실행 E2E 6/6을 통과했습니다.
- [x] `npm run check`: unit 50/50, 전체 E2E 7/7, lint, typecheck, build 통과. 각 test가 CLI/fake child exit를 확인했습니다.
- [x] Opt-in 실제 `agy` smoke를 추가해 응답은 trim 후 SHA-256만 비교하고 stderr는 byte 수만 집계하며 ID·usage·환경값은 출력하지 않도록 했습니다.
- [x] 최초 real smoke에서 `SUCCESS` result의 `error` key 생략을 발견해 parser가 `null`로 normalize하도록 regression을 추가했습니다.
- [x] 실제 `agy 1.2.14` ACP initialize→new→prompt→close smoke 1/1 통과, CLI와 child 정상 종료, 응답 hash 일치.
- [x] Real regression 반영 후 `npm run check`: unit 50/50, mock E2E 7/7, lint, typecheck, build 통과. Real smoke는 opt-in으로 별도 1/1 통과.
- [x] Unscoped npm `agy-acp@0.5.2`가 다른 repository 소유임을 확인하고 package를 `@kcrong/agy-acp@0.1.0`으로 scope했습니다. Executable 이름은 `agy-acp`를 유지합니다.
- [x] Apache-2.0 공식 원문, README, CONTRIBUTING, SECURITY, repository links, semantic version, `private:true` accidental-publish guard를 구성했습니다.
- [x] `npm pack --dry-run --ignore-scripts` 결과가 LICENSE, README, compiled JS/declarations, package.json만 포함하고 source map·tests·tasks·roadmap·환경 파일을 제외함을 확인했습니다.
- [x] checkout v7.0.1과 setup-node v7.0.0의 tag commit SHA를 pin한 Node 22/24·Linux/macOS/Windows CI를 작성하고 YAML parsing을 통과했습니다.
- [x] Public metadata 반영 후 `npm run check`: unit 50/50, mock E2E 7/7, lint, typecheck, build 통과.
- [x] 실제 package tarball을 session scratch clean project에 설치하고 packaged `agy-acp` bin→fake child ACP smoke를 통과했습니다.
- [x] Tracked files, patch history, installed package의 credential·machine-path content scan은 clean입니다.
- [x] Hosted CI run 36696258541의 Linux/macOS/Windows 4개 job 모두 `runner_id=0`, steps `[]`로 runner allocation 전에 실패해 Actions 사용량 소진임을 확인했습니다.
- [ ] Root `21334d1`과 review tip `adfc153`의 author/committer metadata에 machine-specific identity가 있습니다. Public 전 coordinated history rewrite에는 명시적 승인이 필요합니다.

## 독립 리뷰 1차 결과

- Runtime/protocol: Critical 0, High 4, Medium 8, Low 4 — FAIL.
- Public release: Critical 0, High 2, Medium 5, Low 3 — NOT READY.
- High remediation: prompt activation 전 cancel 유실, 이전 process 종료 전 lazy restart, disconnect 중 startup 누수/closeAll barrier 부재, downstream notification backpressure 부재.
- Medium remediation: stdout EOF/child close ordering, write failure retire, session admission limit, process-tree termination, executable absolute resolution, concurrent load reservation, SDK request AbortSignal, event phase ordering, invalid path error mapping, backend text 기반 cancel 오분류.
- Public 후속: packed clean smoke는 이미 local 통과했습니다. Windows process E2E와 hosted matrix는 Actions quota 복구 후 필요하며 `private:true` 제거와 root history rewrite는 실제 공개 release gate로 유지합니다.
- Low corrections: packaged README의 SECURITY link, CI npm version 계약, unknown-event diagnostic 문서 정확성.

## 리뷰 지적 수정 기록

- [x] cancel-before-active: prompt를 첫 await 전에 reserve하고 cancel marker를 `runTurn` 직전에 재검사합니다.
- [x] retiring overlap: controller pointer를 shutdown 완료 전까지 유지하고 같은 session restart가 retirement barrier를 기다립니다.
- [x] disconnect startup leak: manager closed state, tracked startup, shared idempotent `closeAll` barrier로 late controller를 닫습니다.
- [x] concurrent load: session ID를 factory await 전에 reserve해 duplicate controller 생성을 차단합니다.
- [x] Unexpected child exit 뒤 `close()`가 새 7초 timer를 만들던 E2E regression을 즉시-resolved cleanup으로 수정했습니다.
- [x] SessionManager 11/11 + ProcessController 13/13, targeted 합계 24/24, failure E2E 6/6, typecheck, lint 통과.
- [x] Downstream notification backpressure: awaitable event listener와 serialized batch queue를 추가하고 slow consumer 동안 child stdout을 pause/resume합니다. Listener rejection은 즉시 typed failure로 shutdown합니다.
- [x] Backpressure targeted tests 2/2 포함 전체 unit 57/57, mock E2E 7/7, lint, typecheck, build 통과.
- [x] Medium: child `exit`는 stdio drain 전 실패시키지 않고 `close`+event queue barrier에서 처리하며, result 없는 stdout EOF는 즉시 `INVALID_OUTPUT`입니다.
- [x] Medium: stdin write/drain failure가 controller fatal shutdown·retirement로 이어집니다.
- [x] Medium: duplicate init, idle step/result, contradictory SUCCESS/error·ERROR/no-error를 fail-close합니다.
- [x] Terminal ordering/event invariant targeted 31/31, typecheck, lint 통과.
- [x] Medium: SDK `cancellationSignal`→handler AbortSignal→session cancel을 연결하고 `-32800` request-cancelled로 응답합니다.
- [x] Medium/Low: relative path/config는 `-32602`, backend identity/manager fault는 `-32603`, capacity는 bounded server error로 분류합니다. Backend error text로 cancel을 추론하지 않습니다.
- [x] Medium: 기본 16개의 active+starting session admission limit과 `AGY_ACP_MAX_SESSIONS` override를 추가했습니다.
- [x] Request/admission/error targeted 23/23, relative cwd real CLI E2E regression 추가.
- [x] Medium: server startup에서 bare `agy`를 absolute PATH entry로 resolve하고 configured relative path를 거부하며 realpath를 session cwd 이전에 고정합니다.
- [x] Medium: POSIX detached process group signal과 Windows absolute `taskkill.exe /T` tree strategy를 구현했습니다.
- [x] Executable/tree targeted 35/35, 전체 unit 76/76, mock E2E 8/8, real agy 1/1, lint, typecheck, build 통과.
- [x] Public/Low: `--agy-arg` static prefix argv로 Node fake script를 Windows에서도 shell 없이 실행하고 CI Windows E2E를 활성화했습니다.
- [x] Public/Low: package에 SECURITY.md를 포함하고 npm `>=10.9.0 <12` 지원 범위를 문서화했습니다.
- [x] Public/Low: unknown events는 raw payload 없이 조용히 무시하는 실제 동작으로 문서를 정정했습니다.
- [x] Public corrections 후 unit 77/77, mock E2E 8/8, package SECURITY inclusion, Windows E2E YAML 검증 통과.
- [x] Publication remediation: `scripts/package-smoke.mjs`가 actual tarball pack→scratch clean install→package import→packaged CLI/fake child ACP smoke→cleanup을 수행합니다.
- [x] CI Ubuntu Node 24 package step을 dry-run에서 actual `npm run test:package`로 강화했습니다.
- [x] Package remediation 후 unit 77/77, mock E2E 8/8, packaged import/bin smoke 통과.
- [x] Exact-head 독립 재리뷰 2개를 수행했습니다.
- Runtime/protocol 2차: Critical 0, High 3, Medium 5, Low 2 — FAIL.
- Public release 2차: Critical 0, High 0, Medium 1, Low 4 — local code/package PASS, hosted matrix evidence 부족.
- 남은 High: async stdin EPIPE unhandled, close 중 session ID/admission 조기 해제, failed init 종료 전 startup barrier 해제.
- 남은 Medium: new/load request cancellation, active close cancellation semantics, lazy-start cancel arbitration, result listener barrier, Windows taskkill failure fallback.
- 남은 Low/current: busy error classification, streamed final suffix, design-doc 3곳과 PR evidence drift.
- Publication-only: root metadata rewrite, hosted macOS/Windows green, Windows package shim, npm scope ownership, private vulnerability reporting, release 때 `private:true` 제거.
- [x] High: persistent stdin error listener와 write callback+drain barrier로 async EPIPE를 `WRITE_FAILED` fatal shutdown으로 전환합니다.
- [x] High: close 중 session ID와 admission slot을 process retirement 완료까지 유지합니다.
- [x] High: pre-init failure는 child shutdown/close barrier 후에만 `start()`를 reject해 failed startup retry가 process cap을 우회하지 못합니다.
- [x] High barrier targeted 34/34, 전체 unit 80/80, mock E2E 8/8, lint, typecheck, build 통과.
- [x] Medium: new/load SDK request AbortSignal을 child startup까지 전파하고 late controller/session cleanup 후 `-32800`으로 응답합니다.
- [x] Medium: active close는 prompt를 cancel semantics로 종료하며 cancel failure에도 session reservation을 finally에서 제거합니다.
- [x] Medium: lazy restart startup cancellation이 timeout/startup failure보다 우선하고 result listener 성공 후에만 terminal result를 resolve합니다.
- [x] Medium: Windows `taskkill.exe` asynchronous failure를 direct-child signal로 안전하게 fallback합니다.
- [x] Low: `SESSION_BUSY`/`SESSION_EXISTS`를 distinct `-320xx` server-state errors로 분류하고 partial streamed response는 검증된 final suffix만 전송하며 불일치는 fail-closed합니다.
- [x] 설계 문서의 unknown-event logging, prompt-timeout configuration, Windows tree termination, final suffix 설명을 실제 구현과 일치시켰습니다.
- [x] Remediation targeted 60/60, 전체 unit 89/89, mock E2E 8/8, lint, typecheck, build, packed clean-install/import/bin smoke, 실제 authenticated agy smoke 1/1 통과.
- [x] `adfc153` exact-head 3차 independent review를 수행했습니다.
- Runtime/protocol 3차: Critical 0, High 0, Medium 2, Low 1 — merge PASS. Retirement 대기 cancel arbitration, non-zero exit/result arbitration, Windows detached test expectation을 지적했습니다.
- Public-release 3차: current merge Critical 0, High 0, Medium 0, Low 1; publication-only High 1, Medium 3, Low 2. Security ID wording 외 code merge blocker는 없고 hosted matrix/history/release controls는 publication gates입니다.
- [x] Retirement/startup await마다 cancellation을 closed state보다 우선하고 cancel→queued prompt→close race가 replacement child 없이 `CANCELLED`로 끝나도록 수정했습니다.
- [x] Child exit code/signal을 보존해 event-listener drain 중 관측된 non-zero exit가 terminal SUCCESS보다 우선하도록 수정했습니다.
- [x] Windows detached expectation을 platform-aware하게 만들고 taskkill asynchronous fallback 및 incompatible final response fail-closed tests를 강화했습니다.
- [x] SECURITY의 ACP-required session ID와 diagnostics privacy를 구분하고 README에 upstream `agy` data-use/opt-out notice를 연결했습니다.
- [x] Package smoke가 모든 OS에서 `npm exec --offline -- agy-acp`로 installed bin을 호출하며 CI Windows job에서도 clean package smoke를 실행하도록 확장했습니다.
- [x] Post-review targeted 63/63, 전체 unit 92/92, mock E2E 8/8, lint, typecheck, build, packed clean-install/import/bin smoke, 실제 authenticated agy smoke 1/1 통과.
- [x] `74ccd49` focused runtime re-review: Critical 0, High 0, Medium 0, Low 0 — merge PASS.
- `74ccd49` focused public re-review: current merge Critical 0, High 0, Medium 2, Low 0 — HOLD. Rejected retirement/startup await cancellation precedence와 Windows npm shim environment를 지적했습니다.
- [x] 모든 retirement/startup await rejection이 aborted signal을 먼저 확인해 underlying error 대신 `CANCELLED`를 반환하도록 `awaitWithCancellation` barrier를 추가했습니다.
- [x] Retirement reject 및 lazy startup reject after close regressions를 추가해 cancellation precedence와 session reservation cleanup을 검증했습니다.
- [x] Package smoke가 expected local `agy-acp`/`agy-acp.cmd` shim 존재를 먼저 검증하고 `PATHEXT`, `ComSpec`, `SystemRoot`, temp/user variables를 포함한 platform environment를 보존합니다.
- [x] Second remediation 전체 unit 94/94, mock E2E 8/8, lint, typecheck, build, packed clean-install/import/local-bin smoke, 실제 authenticated agy smoke 1/1 통과.
- [ ] Second successor exact head focused independent re-review.


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
- [x] ACP AgentApp baseline handlers
- [x] stdio CLI와 fake-agy normal E2E
- [x] failure·cancel·concurrent E2E matrix
- [x] 실제 agy ACP smoke
- [x] 공개 배포 문서·metadata·CI
- [x] clean package install·content scan·hosted CI
- [ ] 독립 리뷰·history metadata 결정·PR 완료

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
