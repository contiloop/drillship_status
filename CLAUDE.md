# Drillship Status

Transocean·Valaris·Noble·Seadrill 4개 회사의 등록 drillship 62척에 대해 계약 일정(Fleet Status bar)과 일당(Dayrates)을 보여 주는 공개 정적 대시보드다(React/TypeScript/Vite, Vercel 배포, 로그인·편집 없음). 계약 데이터는 공식 Fleet Status 보고서와 공식 기사를 운영자의 Mac에서 Codex CLI가 읽어 채우고, Python 자동화는 수집·호출·화면 안전 확인·나눗셈·기록·게시만 한다. 운영자는 개인 한 명이며, 규모에 비해 무거운 인프라(DB, 서버 상주 프로세스, 관리 화면)를 들이지 않는다.

## 프로젝트 구조

```
drillship/
├── CLAUDE.md                         ← 진입점 (AGENTS.md와 같은 내용)
├── AGENTS.md                         ← 진입점 (CLAUDE.md와 같은 내용)
├── docs/
│   ├── architecture.md               ← 구성 요소, 서버 감지·Mac 읽기·게시·화면의 흐름과 경계
│   ├── business-rules.md             ← 선박·계약·날짜·요율·판단·통계 규칙
│   ├── security.md                   ← Codex 로그인, 모델 격리, 출처·게시 허용 범위, 기록 대상
│   ├── standards.md                  ← 지켜야 할 규칙, 모듈 경계, 검증 관문
│   ├── engineering-notes.md          ← 함정과 비자명한 동작 (재읽기, 지문, 실행기, 캐시)
│   ├── operations.md                 ← 설치·검증·실행·전환·진단 절차
│   ├── contracts.md                  ← 공개 JSON, 작업 대기열, Codex 응답, CLI 명령의 계약
│   └── tracking/
│       ├── status.md                 ← 현재 위치와 남은 일 (운영 전환)
│       ├── findings.md               ← 아직 풀지 못한 문제
│       └── decisions/
│           ├── index.md              ← 결정 기록 목록
│           └── 0001~0009-*.md        ← 개별 결정
├── automation/
│   └── AGENTS.md                     ← Python 수집·읽기·기록·게시 모듈의 범위·불변식·테스트
└── components/
    └── AGENTS.md                     ← 화면 계층(App.tsx, components/, utils/, types.ts, tests/)의 범위·불변식·테스트
```

## 절대 규칙

- **자연어 해석은 Codex만 한다.** 날짜·기간·금액·요율·계약 종류·같은 계약 판단·기사 선별을 코드 규칙(정규식, 회사별 예외표)으로 구현하지 않는다. 코드의 확인은 화면 안전(등록 선박·기존 계약 ID·ISO 날짜와 순서·유한한 0 이상 수치·허용값·62척 무결성)에 한정한다.
- **읽기는 Codex CLI + ChatGPT 로그인 + `gpt-6-luna`·Fast off로만(보고서 reasoning high, 그 밖 medium) 한다.** API 키, 다른 모델, 대체 경로를 쓰지 않는다. 사용 한도에 걸리면 해제 시각까지 기다린다.
- **인증값을 문서·로그·저장소에 남기지 않는다.** Codex 인증 파일과 키를 서버(GitHub Actions)나 저장소로 복사하지 않는다.
- **자동 게시는 `public/data`, `data/provenance`, `data/news`의 JSON만 바꾸고, force push하지 않는다.** 생성 데이터(`public/data`, `data/provenance`)는 파이프라인 실행으로만 바뀌며 손으로 고치지 않는다.
- **보고서에서 빠졌다는 이유로 계약을 지우지 않는다.** 명시적 취소·대체가 확인될 때만 철회하고, 철회·수정 전 값은 기록에 남긴다. 등록 선박 62척은 자동으로 추가·삭제하지 않는다.

## 작업 전 확인

항상 `docs/standards.md`, `docs/engineering-notes.md`, 그리고 손댈 모듈의 `AGENTS.md`(`automation/` 또는 `components/`)를 먼저 읽는다. 그 밖에 작업 종류별로:

- **읽기 요청문(`reading_service.py`)을 고칠 때:** 이미 읽은 문서는 다시 읽히지 않는다. 고친 요청문이 기존 데이터에 반영되지 않는 이유와 강제 재읽기 방법을 engineering-notes의 재읽기 항목에서 확인한다.
- **HTML·PDF 추출(`source_materials.py`)을 고칠 때:** 추출 결과가 문서 지문이다. 추출이 바뀌면 이미 읽은 기사 전부가 새 판으로 재등록된다. engineering-notes의 지문 항목과 `npm run data:check` 확인 절차를 먼저 본다.
- **공개 JSON 필드나 `types.ts`를 바꿀 때:** `docs/contracts.md`의 필드 정의와 예전 필드 호환 조항을 확인한다. Python 쪽 형식(`reading_contracts.py`)과 `types.ts`는 함께 바뀌어야 한다.
- **bar·통계·Dayrates 계산을 바꿀 때:** `docs/business-rules.md`의 요율(공시 vs 환산)·날짜 배치·통계 포함 규칙을 확인한다.
- **실행기·게시·운영 전환을 다룰 때:** `docs/operations.md`의 전환 순서와 `docs/tracking/status.md`의 현재 위치를 확인한다. 실행기 코드와 읽기 코드는 서로 다른 checkout에서 돈다(engineering-notes).
- **새 검증 규칙을 넣고 싶을 때:** 결정 기록 0002(화면 안전만 검증)와 0007(겹침은 막지 않고 다시 묻기)를 먼저 읽는다. 사용자가 명시적으로 거부한 방향이다.

## 문제 보고

아래는 발견 즉시 사용자에게 보고한다.

- 프로덕션에 게시된 데이터에서 계약이 대량으로 사라졌거나 62척·4개 회사 무결성이 깨졌다.
- 프로덕션 화면이 내장 데이터(2026-01-07 기준)로 떨어졌다(manifest 검증 실패).
- 허용 경로 밖 파일이 자동 커밋됐거나 force push가 일어났다.
- 인증값·API 키가 로그·저장소·서버에 노출됐거나, Codex가 API 키 모드나 다른 모델로 실행된 흔적이 있다.
- 같은 문서가 실행마다 다시 읽혀 Codex 호출이 반복된다(사용 한도 소진으로 이어진다).

그 밖의 문제는 해결하지 못하면 `docs/tracking/findings.md`에 기록한다.
