# Drillship Status

Transocean, Valaris, Noble, Seadrill의 공식 fleet status report와 공식 IR/SEC 공지를 추적하는 드릴십 대시보드입니다.

프로덕션: <https://offshore-drillship-fleet-analyst.vercel.app>

## 자동 갱신 흐름

1. 서버(GitHub Actions, 6시간마다): 공식 보고서 PDF·SEC HTML·뉴스를 수집하고 문서 내용 지문(`documentSha256`: PDF는 파일 바이트, 기사 HTML은 본문 글자)을 기록과 비교합니다. 기사 주변의 주가 표시처럼 본문 밖이 바뀌어도 같은 문서로 봅니다. 새 보고서 revision은 `fleet-report`, 새 기사는 `official-news` 작업으로 `manifest.newsQueue`(schemaVersion 2)에 올립니다. 서버는 모델을 호출하지 않고 계약 데이터를 바꾸지 않습니다. 접근 실패는 HTTP 상태와 마지막 성공 시각을 남기고 기존 데이터를 유지합니다.
2. Mac 실행기(launchd, 5분마다): 프로덕션 큐에 대기 작업이 있을 때만 임시 clone에서 로컬 실행을 시작합니다.
3. 로컬 실행: 새 기사 제목을 한 번의 Codex 호출로 분류(title triage)하고, 선택되지 않은 기사는 본문을 읽지 않고 제목+링크로만 남깁니다. 선택된 기사와 보고서는 ChatGPT 로그인 Codex CLI(`gpt-6-luna`, medium)로 하나씩 읽고, 코드는 화면 안전성(등록 선박·기존 계약 ID·날짜·숫자·상태값)과 전체 데이터 무결성만 검사한 뒤 통과 항목만 적용합니다.
4. 같은 문서(같은 sourceId·같은 원본 해시)는 다시 읽지 않습니다. 프롬프트나 baseline이 바뀌어도 재큐하지 않습니다.
5. 테스트·빌드를 통과한 JSON만 비강제 push하고, main 경합 시 새 clone에서 재시도하며, Vercel 배포 내용이 일치해야 완료로 봅니다. Codex 사용량 한도에 걸리면 실패로 세지 않고 재설정 시각까지 기다립니다.

읽기 실패 자료의 기존 계약과 완료된 과거 계약을 보존합니다. 새 보고서의 누락만으로 계약을 삭제하지 않으며, 모델이 명시적 변경·취소 근거를 확인한 경우에만 수정·철회합니다. 미공개 공시 일당은 `0`, 백로그 환산액은 `interpretation`에 별도 보관합니다.

## 계약표 중심 화면

첫 화면과 회사별 화면은 계약 바를 우선 표시합니다. 바를 클릭하거나 키보드로 선택하면 기간·고객·지역·공개된 일당과 뉴스 출처를 확인할 수 있습니다. 월 단위 날짜는 월 단위로 표시하고, 완료된 과거 계약은 현재 타임라인에 그리지 않습니다. 시장 분석 차트는 계약표 아래에서 펼쳐볼 수 있습니다.

계약표를 처음 열거나 회사를 바꾸면 현재 분기부터 보이도록 가로 스크롤 위치를 맞춥니다. 이전 분기도 왼쪽으로 스크롤해 확인할 수 있으며, 계약의 원래 날짜는 바꾸지 않습니다. 분기는 브라우저의 현재 날짜로 계산합니다(10–12월은 Q4).

**뉴스·공시** 탭(`#news`)은 모든 보고서·기사를 날짜순 한 목록으로 보여 주고, 항목마다 종류 태그(계약 · 실적 · 일정 · 자금·주주 · 경영·인사 · Fleet Status · 기타)를 붙입니다. 계약 조건이 있는 항목은 접힌 카드로 열어 볼 수 있습니다. 내부 모델명·설정·대조 과정은 화면에 노출하지 않고 증거 원장에 보관합니다.

### 계약 상태와 백로그 환산 요율

- `utils/newsEconomics.ts`는 모델이 남긴 `interpretation`을 화면 값으로 투영합니다. 문장에서 날짜·금액·기간을 다시 해석하지 않습니다.
- 같은 기본 수주의 금액과 작업 기간이 모두 확인되면 `계약 가치 ÷ 기간(일)`을 **$…k**로 바 안에 바로 표시합니다. '추정' 꼬리표는 붙이지 않고 선택 상세에서 공시값/백로그 환산값을 구분합니다. 1년=365일, 1개월=365/12일 가정은 선택 상세에 명시합니다. 최대 기간만 공개된 경우에는 단일 요율을 계산하지 않습니다.
- 추가 서비스·이동 비용 포함/제외, 계산식, 원문은 바를 눌러 확인합니다. 이 값은 계약 가치의 일평균 환산액이며 공시 operating dayrate가 아닙니다. 미공개 비용을 임의로 빼지 않습니다.
- 연장 구간별 금액·대응 기간은 모델이 선택하고 각각 row로 보존합니다. Codex는 금액과 기간(일수)만 주고, 나눗셈은 코드가 하며 합산 수주액을 선박마다 중복 배분하지 않습니다.
- 새 수주는 검증·적용된 canonical row를 기존 Fleet Status 바 스타일로 표시합니다. 모델이 날짜 배치와 상태를 결정하고 원문 정밀도·한국어 배치 근거를 보존합니다. LOA나 미행사 옵션을 Firm으로 승격하지 않습니다.
- 이벤트의 미적용 row에서 별도 계약 바를 만들지 않습니다. 적용 이벤트의 `readingDecision.contractId`와 `row.id`는 canonical 계약 ID를 가리킵니다.
- 대략적인 일정(mid 2027, Q4 2027 등)은 Codex가 구체 날짜로 배치하며(예: mid → 6월 무렵), 별도 표시 없이 막대와 통계에 그대로 씁니다. 이후 공시가 나오면 고쳐집니다. 백로그 환산 요율은 평균 공시 dayrate에 넣지 않습니다. 별도 Dayrates 탭에서는 아래 규칙에 따라 구별해서 표시합니다. 체결 여부와 원문 날짜 정밀도는 따로 보존하며, 표시 이름만 바꿔 미공개 요율을 공시값으로 기록하지 않습니다. 금액·기간이 없거나 인용이 모호한 경우에는 요율을 생성하지 않고 뉴스에 근거를 남깁니다.

### 세대별 Dayrates

`#dayrates` 탭은 6G·7G·7G+·8G를 개별 선택하는 검은 배경의 산점도입니다. 채운 파란 다이아몬드는 공시 일당, 빈 다이아몬드는 백로그 환산액입니다. 점의 면적은 기본 계약 구간 길이에 비례하며 4년에서 상한을 둡니다. 점을 클릭하거나 키보드로 선택하면 기간·요율·계산식·날짜 배치 근거·원문 링크를 표시합니다. 원천 데이터 갱신 시 차트도 같은 투영 함수로 다시 계산합니다.

- `utils/dayrateTrends.ts`는 UI와 분리된 데이터 투영·통계 모듈입니다. 검증된 Firm 구간만 사용하고, 옵션·조건부 수주·미공개 요율·개별 날짜가 없는 연장 요율은 제외합니다. 원본 fleet/events를 수정하지 않습니다.
- 가로축은 **계약 구간 시작일**이며 수주 발표일이 아닙니다. 미래 개시 계약을 포함하고, 월·분기 단위 개시는 기존 Fleet Status의 배치 기준을 따릅니다. 현재 확보한 계약 구간이지 2021년 이후 전체 수주 이력이나 추가 서비스 제외로 보정한 시장 지수는 아닙니다. 장기 계약의 최신 공시 일당은 최초 개시 당시의 일당을 뜻하지 않습니다. 참고 이미지의 99건 원자료는 포함되어 있지 않습니다.
- 기본값은 7G, 백로그 환산 포함입니다. 체크를 끄면 공시 일당만 남습니다. 선택값은 `dayrateGen`, `dayrateBasis` URL query에 저장하여 새로고침·링크 공유 시 유지합니다.
- 7G+는 2025년 1월 1일 이후 시작한 계약 구간만 표시합니다. 가로축은 2025년부터, 표본 수·LOESS·음영도 같은 구간만 사용합니다. 다른 세대의 범위와 원본 계약 이력은 그대로 유지합니다.
- 추세선은 [d3-regression](https://github.com/HarryStevens/d3-regression)의 robust LOESS를 사용합니다(2.2.0 고정, lockfile로 재현). 기본 span 0.30, 소표본은 최소 4개 이웃을 확보하도록 span을 늘립니다. 5구간 또는 서로 다른 시작일 4개 미만이면 선과 밴드를 숨깁니다. 관측 구간 밖으로 외삽하지 않습니다.
- 음영은 인접 계약 구간의 LOESS 잔차에 대한 ±1 **표본 표준편차**입니다. 신뢰구간·예측구간이 아닙니다. 각 구간을 동일 가중치로 계산하고 계약 기간은 점 크기에만 반영합니다. [LOESS 방법 설명(NIST)](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm).
- `tests/dayrateTrends.test.cjs`는 실제 데이터의 세대별 표본·출처·환산 근거, 제외 규칙, 소표본, 선형/상수/잡음 데이터의 통계 계산, 점 면적, 축 범위를 검증합니다.

## 운영: 복원·실행기·진단

복원(새 Mac 또는 재설치):

```bash
brew install python@3.12 node@22 tesseract
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r automation/requirements.lock
npm ci
codex login            # ChatGPT 로그인 (API 키 사용 안 함)
```

서버 workflow는 저장소 Actions 변수 `FLEET_READER_V2_ENABLED=true`일 때만 실행됩니다(실행기 설치 후 켭니다).

실행기 설치/제거(launchd `com.contiloop.drillship-news-worker`, 5분 간격):

```bash
.venv/bin/python -m automation.install_news_worker            # 설치
.venv/bin/python -m automation.install_news_worker --uninstall
launchctl print gui/$(id -u)/com.contiloop.drillship-news-worker
```

수동 실행(실제 수집·Codex 호출·push를 수행):

```bash
.venv/bin/python -m automation.news_worker                    # 큐 확인 후 필요할 때만 게시
.venv/bin/python -m automation.update_news                    # 큐와 무관하게 로컬 읽기 + 게시
.venv/bin/python -m automation.update_news --stored-results   # 1회 복구: data/news/reviews.json 저장 결과 반영
```

`--stored-results`는 저장된 리뷰마다 `stored:<url>` 작업을 한 번만 만들고(완료 기록이 재실행을 막음) 현재 fleet를 baseline으로 읽어 적용합니다. 반영된 행에는 `inputMode: "stored-result"`와 `storedReading.reviewedAt`이 붙습니다.

진단:

- `data/provenance/readings.json`: 문서별 기록(`status` pending/failed/completed, `outcome`, `lastError`, `failureCount`, `nextAttemptAfter`, `openHolds`, 변경 전 값 `changes`)과 최근 수집 오류.
- 공개 `manifest.json`의 `readingStatus`(마지막 수집/읽기/적용 시각, `usageLimitedUntil`, collectionErrors, readingErrors)와 `sources`/`sourceHealth`.
- 실행기: `.cache/news-worker-state.json`(대기 시각·오류), `.cache/news-worker.log`, `.cache/news-worker-error.log`. 응답 캐시 `.cache/reading-responses.json`은 재시도 시 모델 재호출을 막는 로컬 파일이며 Git에 올리지 않습니다.
- 기술 실패는 5분부터 두 배씩 최대 6시간 백오프로 해당 작업만 재시도하고, 같은 실행의 성공 결과는 게시됩니다. 구 기록(`data/provenance/sources.json`, `observations.json`, `data/news/reviews.json`)은 최초 이전(migration)과 저장 결과 복구에만 읽고 다시 쓰지 않습니다.

## 공식 원천

- [Transocean Fleet Status Report](https://www.deepwater.com/investors/fleet-status-report)
- [Valaris Investors / Fleet Status Report](https://www.valaris.com/investors/) (SEC submissions CIK 0000314808도 우선 확인)
- [Noble Fleet Status Report](https://noblecorp.com/our-investors/reports-filings/fleet-status-report/)
- [Seadrill Fleet](https://www.seadrill.com/fleet/)

한국 DART 공시는 사용하지 않습니다.

## 로컬 실행

```bash
npm run dev
npm run data:check     # 서버 모드 수집만, 파일 쓰기 없음 (네트워크 사용)
npm run verify         # .venv/bin/python 테스트 + UI 테스트 + 빌드
```

## 생성 산출물

- `public/data/manifest.json`: 데이터 해시, 보고서 기준일·출처 상태, `newsQueue`, `readingStatus`
- `public/data/fleet.<hash>.json`: 62척 fleet data
- `public/data/events.<hash>.json`: 공식 기사 목록(제목+링크)과 읽기 결정별 이벤트(`readingDecision`)
- `public/data/changes.json`: 직전 데이터와의 변경 요약
- `data/provenance/readings.json`: 문서 기록(위 진단 참고)

## 데이터 규칙

- 계약 상태: `Firm`, `Option`, `Contingent`
- 선박 상태: `Active`, `Idle`, `Warm-Stacked`, `Cold-Stacked`
- 날짜·기간·정밀도와 ISO 표시 배치는 모델의 판단과 한국어 근거를 보존합니다.
- 경제적 가동률은 `Firm` 구간의 합집합으로 계산하여 같은 달 전환 계약의 겹침을 이중 계산하지 않습니다.
- total contract value÷기본 기간은 화면 전용 환산값으로 표시하고, 공시 dayrate 및 확정 일자 기준 통계와 구분합니다.
- 최신 보고서에 없는 계약도 유지하며 명시적인 모델 withdraw만 철회합니다. 변경 전 값과 완료된 과거 계약을 보존합니다.
- 선박의 원문 상태에는 `statusAsOf`를 함께 저장하며, 그 이후 시작한 현재 Firm 계약만 화면의 유효 상태를 Active로 전환합니다.

## 운영 명령

```bash
.venv/bin/python -m automation.fleet_sync --write --mode server   # 서버 수집 + 큐 게시 (Actions와 동일)
.venv/bin/python -m automation.fleet_sync --write --mode local    # 이 checkout에서 로컬 읽기 (push 없음)
.venv/bin/python -m pytest automation/tests -q
npm run build
```

보고서 기준일을 확인하지 못하면 마지막으로 확인된 날짜를 유지하며 수집 시각으로 대체하지 않습니다. 통합 무결성 검사에 실패한 문서는 전체를 적용하지 않고 해당 작업만 실패 처리합니다. Ubuntu에서는 `tesseract-ocr tesseract-ocr-eng`가 필요하며 workflow가 설치합니다.
