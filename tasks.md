# agy-acp 작업 기록

## 상태

- 현재 단계: 프로토콜 계약 확정
- 작업 branch: `feat/agy-acp-bridge`
- draft PR: #2 (`feat(acp): add antigravity ACP bridge`)
- 다음 작업: `step_update`의 비민감 nested key 구조를 수집하고, malformed input·process cancellation fixture를 확보합니다.
- 완료 조건: `roadmap.md`의 모든 checkbox 완료, local gate 통과, 독립 리뷰 완료, PR merge

## 조사 메모

- `--print`는 문자열 인자를 요구합니다. stream mode 시작은 다른 flag 뒤에 `--print=''`를 명시합니다.
- stream input 최상위에는 `event`가 필수이며, 정상 user turn은 `{"event":"user","message":...}` 형식을 사용합니다.
- 알 수 없는 input event는 warning 후 무시되며 process는 성공 종료합니다.
- 정상 출력 순서는 `init` → `step_update` 1개 이상 → `result`입니다.
- `init`은 `cwd`, `permission_mode`, `tools`를 포함하고, terminal `result`는 `conversation_id`, `duration_seconds`, `error`, `num_turns`, `response`, `status`, `usage`를 포함합니다.
- 실제 최소 turn은 `result.status=SUCCESS`로 완료됐으며 응답 본문과 자격 증명은 출력하지 않았습니다.
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

## 진행 중

- [ ] `agy` stream-json protocol fixture 수집
- [ ] ACP v1 method·capability 목록 확정
- [ ] ACP↔`agy` 변환 계약 작성

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
