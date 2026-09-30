# agy-acp 작업 규칙

이 문서는 `agy-acp` 저장소의 모든 구현 및 변경 작업에 적용합니다.

## 1. 브랜치 및 코드 리뷰

- `main`, `master` 등 default branch에 직접 push하지 않습니다.
- 변경 작업은 항상 최신 default branch를 기준으로 feature branch를 생성한 뒤 진행합니다.
- push할 때는 대상 feature branch를 명시합니다.
- 모든 변경은 반드시 PR을 생성하고 코드 리뷰를 거친 뒤 merge합니다.
- 리뷰에서 확인된 문제를 반영하고 필요한 검증을 마치기 전에는 merge하지 않습니다.

## 2. PR 제목 및 본문

- PR 제목은 영문으로 간결하게 작성합니다.
- PR 본문은 한국어로 작성합니다.
- 핵심 내용만 간결하게 작성하고, 변경을 이해하는 데 필요할 때만 배경 설명을 추가합니다.
- 다음 섹션을 순서대로 사용합니다.

```markdown
## 무엇을 위해

## 어떤 변경을

## 왜

## 어떻게 테스트했는지
```

- `어떻게 테스트했는지`에는 실제 실행한 테스트와 결과를 적습니다.

## 3. 기능 테스트

- 기능의 신뢰성(reliability)을 최우선으로 검증합니다.
- 실제 사용자 흐름을 확인할 수 있도록 end-to-end 테스트를 최대한 활용합니다.
- 정상 흐름뿐 아니라 주요 실패·취소·경계 조건도 가능한 범위에서 검증합니다.
- 장시간 실행하거나 대화형 프로세스를 검증할 때는 필요에 따라 `screen` 또는 `tmux`를 사용합니다.
- 단위 테스트와 통합 테스트는 end-to-end 테스트를 보완하는 용도로 함께 사용합니다.
- 실행한 테스트 명령과 결과를 PR 본문에 기록합니다.

## 4. Git commit message

- 모든 commit message는 Conventional Commits 형식을 사용합니다.

```text
<type>(<scope>): <description>
```

- 대표 type은 `feat`, `fix`, `test`, `refactor`, `docs`, `chore`, `ci`, `build`, `perf`입니다.
- 하나의 commit에는 하나의 논리적 변경만 담습니다.
- 예: `feat(acp): add agy process bridge`
