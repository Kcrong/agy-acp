# agy-acp 로드맵

## 목표

`agy`(Antigravity CLI)를 표준 ACP(Agent Client Protocol) v1 agent로 사용할 수 있는 독립 실행형 `agy-acp` 프로그램을 구현합니다. ACP client가 `agy-acp`를 하위 프로세스로 실행하고 JSON-RPC over stdio로 세션을 생성하며, prompt·streaming update·취소·오류를 안전하게 주고받을 수 있어야 합니다.

## 기본 결정

- 개발·검증 기준 런타임은 Node.js 24, 언어는 TypeScript strict ESM을 사용합니다.
- 배포물은 compile된 JavaScript와 `agy-acp` executable을 제공하며 사용자가 TypeScript runtime을 설치하지 않아도 됩니다.
- 향후 public repository와 npm package로 배포합니다. 최소 Node version은 SDK 호환성과 local matrix 검증을 통과한 maintained LTS로 정하고 Node.js 24 전용 API에 불필요하게 종속되지 않습니다.
- Linux·macOS·Windows에서 사용자 홈이나 절대 경로를 가정하지 않고, `agy` 실행 파일 탐색 실패를 명확히 진단합니다.
- 최신 안정 ACP v1과 공식 `@agentclientprotocol/sdk`의 fluent `agent()` API를 기준으로 합니다. ACP v2 Draft는 범위에서 제외합니다.
- `agy --input-format stream-json --output-format stream-json` NDJSON 인터페이스를 사용하며 터미널 화면을 파싱하지 않습니다.
- 기존 `agy` 로그인 상태를 그대로 사용하되 자격 증명·토큰 파일을 직접 읽거나 출력하지 않습니다.
- `--dangerously-skip-permissions`는 기본값으로 사용하지 않습니다.
- `stdout`에는 ACP 메시지만 쓰고 로그와 `agy` 진단 출력은 `stderr`로 분리합니다.
- 우선 Linux에서 실제 E2E를 검증하고, 프로세스 추상화와 mock E2E로 macOS·Windows 동작을 검증 가능하게 설계합니다.
- GitHub Actions 실패는 사용량 소진으로 runner가 시작되지 않은 경우에만 일시적으로 허용합니다. 코드·테스트 실패는 허용하지 않습니다.

## 1. 프로토콜 계약 확정

- [x] 설치된 `agy`의 stream-json 입력·출력 event를 정상·오류·취소 흐름별 fixture로 확보합니다.
- [x] ACP v1 필수 method와 선택 capability를 분류합니다.
- [x] ACP request/update와 `agy` event 간 변환 표를 작성합니다.
- [x] 세션, 동시성, 취소, timeout, process 종료 정책을 결정합니다.
- [x] 지원하지 않는 ACP capability는 광고하지 않고 명시적인 protocol error를 반환하도록 정의합니다.

## 2. 프로젝트 기반 구축

- [x] Node.js 24 + TypeScript strict ESM 프로젝트를 구성합니다.
- [x] 의존성은 정확한 버전으로 고정하고 lockfile을 commit합니다.
- [x] `agy-acp` executable entry point와 package `bin`을 구성합니다.
- [x] typecheck, lint, unit test, E2E test, build 명령을 구성합니다.
- [x] 메시지 크기, line 길이, timeout 등 안전 한도를 중앙 설정으로 둡니다.

## 3. `agy` process bridge 구현

- [x] shell 없이 인자 배열로 `agy`를 실행합니다.
- [x] 분할 chunk를 견디는 bounded NDJSON parser를 구현합니다.
- [x] session별 process lifecycle과 표준입출력 backpressure를 관리합니다.
- [x] 비정상 종료, malformed JSON, stderr, timeout을 typed error로 변환합니다.
- [x] ACP 취소를 현재 turn 중단 후 제한 시간 내 process 종료로 연결합니다.
- [x] 종료 시 child process와 listener를 누수 없이 정리합니다.

## 4. ACP agent 구현

- [x] ACP initialize 및 실제 지원 capability 협상을 구현합니다.
- [x] session 생성과 고유 ID·working directory 관리를 구현합니다.
- [x] prompt를 `agy` input으로 변환하고 응답을 ACP streaming update로 전달합니다.
- [x] 세션 취소와 client disconnect 정리를 구현합니다.
- [x] 여러 ACP session이 한 연결에서 독립적으로 동작하도록 구현합니다.
- [x] ACP JSON-RPC error code와 사용자용 오류 메시지를 안정적으로 반환합니다.

## 5. 신뢰성 테스트

- [x] parser, event mapping, session manager, error mapping, cancellation unit test를 작성합니다.
- [x] fake `agy`를 이용한 ACP client-to-process mock E2E harness를 작성합니다.
- [x] 정상 streaming, 분할 JSON, malformed JSON, 조기 종료, timeout, 취소, 동시 session을 E2E로 검증합니다.
- [x] 설치된 실제 `agy`로 자격 증명을 노출하지 않는 최소 smoke E2E를 통과시킵니다.
- [x] open handle과 child process 누수가 없음을 검증합니다.
- [x] local typecheck, lint, unit, mock E2E, build를 모두 통과시킵니다.

## 6. 공개 배포·사용 문서와 CI

- [x] 설치, 실행, ACP client 설정, 환경 요구사항, 문제 해결, 호환성 표를 README에 작성합니다.
- [x] package metadata, license, ignore, semantic version 정책, release 전 검증 구성을 정리합니다.
- [x] `CONTRIBUTING.md`, `SECURITY.md`와 공개 issue/PR 기여 기준을 작성합니다.
- [x] npm package가 source map·credential·개인 경로·local fixture를 포함하지 않는지 `npm pack --dry-run`으로 검증합니다.
- [x] session scratch의 clean directory에 package를 설치해 `agy-acp` executable smoke를 수행합니다.
- [x] Linux·macOS·Windows를 대상으로 GitHub Actions workflow를 작성합니다.
- [x] Actions 사용량 소진으로 실행 전 실패하면 원인과 local 검증 결과를 PR에 기록합니다.
- [x] PR-introduced history와 package contents에 secret·token·machine-specific path가 없음을 검사합니다.
- [ ] Public visibility 전 inherited `main` root `21334d1`의 machine-derived Git identity를 별도 default-history rewrite로 정리합니다. 이 항목은 current code merge와 분리된 public-release gate입니다.

## 7. 완료 게이트

- [x] Current code merge scope의 모든 항목에 검증 증거가 있고 `tasks.md`가 최종 상태를 반영합니다. 별도 public-release gate는 아래 미완료 항목으로 유지합니다.
- [x] 실제 `agy` smoke를 포함한 모든 local gate가 통과합니다.
- [x] 독립 코드리뷰에서 Critical·High finding이 0입니다.
- [x] Conventional Commits 형식의 영문 PR 제목과 지정된 English 4-section 본문으로 PR을 생성합니다.
- [x] 리뷰 지적을 반영하고 final head를 재검증해 current merge gate를 통과합니다.
