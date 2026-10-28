# 현재 상태

기준: 2026-10-28, 브랜치 `codex/llm-fleet-updates`(아직 `main`에 반영되지 않음). 프로덕션은 `main`의 예전 파이프라인(대기열 v1)으로 돌고 있다.

## 완료

### 구현되고 테스트로 검증됨

2026-10-28 실행 결과: `.venv/bin/python -m pytest automation/tests -q` 213개 통과, `npm run test:ui` 42개 통과, `npm run build` 성공.

| 영역 | 내용 |
|---|---|
| 응답 형식·화면 안전 확인 | 단순화한 Codex 응답 형식, 항목 단위 보류, 형식 오류 재요청(3번), 환산 일당 코드 계산 |
| 읽기 서비스·Codex 어댑터 | 보고서·기사·저장 결과·제목 선별 요청문, ChatGPT 로그인 확인, 격리 실행, 페이지 이미지 첨부, 사용 한도와 해제 시각 판별, 같은 고객 겹침 재질문 |
| 작업 흐름·기록 | (출처, 지문) 기반 작업 식별, 서버/로컬 모드, 선별 제외, 보류의 다음 읽기 전달과 닫기, 읽는 중 원문 변경 시 미적용, 무결성 실패 시 문서 단위 미적용, 기술 실패 백오프, 예전 기록 이전, 저장 결과 보완 1회 실행, 응답 캐시 |
| 수집 | HTML은 본문 텍스트, PDF는 바이트로 지문 계산(Transocean 주가 위젯으로 인한 반복 재읽기 해결) |
| 실행기·게시 | v2 대기열만 읽는 실행기, 임시 clone 게시·테스트·허용 경로·비강제 push·경합 재시도·배포 내용 확인, launchd 설치기, 변수로 막아 둔 서버 워크플로 |
| 화면 | bar를 계약 row로만 그림, 공시/환산 구분, 대략 날짜 계약의 통계 포함, 예전 필드 호환, 뉴스·공시 탭 날짜순 한 목록과 종류 태그 |

### 실제 자료로 로컬 실행됨 (게시 안 됨, 커밋 `c2a2ad6`)

- 보고서: Transocean·Valaris·Seadrill 3건은 본문이 같아 이전(`migrated`), 새 Noble 보고서(2026-10-26) 1건을 Codex로 읽어 반영.
- 기사: 예전 처리분 28건 이전, 제목 선별로 28건 제외, 9건 본문 읽기(반영 4, 변경 없음 3, 보류 2), 저장 결과 보완 6건 반영.
- 원문 대조로 확인된 것:
  - Noble Faye Kozack–Tullow가 2027-06-01~2028-01-31 Firm과 이어지는 Option으로 들어감.
  - Noble Venturer–Shell 환산 일당 415,068달러.
  - Dhirubhai Deepwater KG2, Deepwater Conqueror(2건), Deepwater Proteus, West Carina, VALARIS DS-18이 저장 결과 보완 row로 존재.
- 결과 차이: Noble Voyager–Shell에는 이번 결과에서 환산 일당이 없다(같은 보고서도 실행마다 결과가 달랐다. 검증 범위 결정에 따라 그대로 둔다).
- Noble `noble.gcs-web.com` 기사 일부는 HTTP 403으로 수집 오류로 남아 있다.

### 검증되지 않음

- 프로덕션 전 구간(서버 감지 → 대기열 게시 → Mac 처리 → 게시 → 프로덕션 payload).
- 프로덕션에서 무변경 수집이 모델 호출 없이 끝나는지(단위 테스트로만 확인).
- 프로덕션 화면(Fleet Status bar·상세·Dayrates·뉴스·공시 탭)에서 새 데이터 표시.

## 남은 일 (순서대로, 사용자 승인 뒤 한 번에)

1. 옛 Mac 실행기 중지.
2. `main` 병합과 Vercel 배포 확인(전환 전 `main` 기준 revision `4d82ab6`).
3. 새 실행기 설치(`python -m automation.install_news_worker`, 설치 checkout을 `main`으로).
4. GitHub Actions 변수 `FLEET_READER_V2_ENABLED=true` 설정 후 서버 감지 실행.
5. 복구 데이터 확인(`python -m automation.update_news --stored-results`). 복구 결과는 브랜치에 이미 커밋되어 있어(`c2a2ad6`) 이 커밋째로 병합하면 병합과 함께 게시되고, 이 실행은 확인만 한다. 데이터 커밋을 포함해 병합할지(포함하지 않으면 이 실행이 복구를 새로 하며 Codex 호출이 다시 든다)는 전환 때 정한다.
6. 프로덕션 확인: 대기열 v2·manifest·payload 반영, 재실행 시 모델 호출 0, 화면 네 영역 정상.

새 회사·선박 등록, 세대 재분류, 관리·승인 화면, 과거 전체 수주 데이터 수집, 화면 디자인 변경은 계획에 없다.

## 막힌 것

- 운영 전환은 사용자 승인 대기다.
- 그때까지 설치된 실행기(`com.contiloop.drillship-news-worker`, 이 checkout의 코드)는 프로덕션이 v1 대기열을 게시하므로 "news queue must be schemaVersion 2"로 5분마다 실패한다. 아무것도 읽거나 게시하지 않으며 전환으로 해소된다.
