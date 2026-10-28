# Drillship Status

Transocean, Valaris, Noble, Seadrill의 공식 fleet status report와 공식 IR/SEC 공지를 추적하는 드릴십 대시보드입니다.

프로덕션: <https://offshore-drillship-fleet-analyst.vercel.app>

## 자동 갱신 흐름

1. GitHub Actions가 6시간마다 공식 PDF·SEC HTML·뉴스 본문을 수집하고 원본 파일 해시 변경을 확인합니다. 서버는 계약을 해석하거나 수정하지 않습니다.
2. `manifest.newsQueue` v2에 보고서와 기사별 읽기 작업을 등록하고 마지막 정상 fleet를 유지합니다. 뉴스 제목이나 보고서 날짜만으로 기사를 제외하지 않습니다.
3. Mac 실행기는 5분마다 큐만 확인합니다. 대기 작업이 있을 때 ChatGPT 로그인된 Codex CLI의 `gpt-6-luna`, medium, Fast off로 전체 본문과 PDF 모든 페이지 PNG를 읽습니다.
4. 모델이 기존 계약 식별·날짜 배치·경제 조건·변경·보류를 판단합니다. 코드는 구조·참조·날짜 순서·계산·원문 인용과 해시만 검증합니다. 유효한 독립 변경은 자동 적용하고 보류는 근거와 함께 완료 처리합니다.
5. 임시 clone에서 테스트와 빌드를 통과한 JSON만 비강제 push합니다. 최신 main 경합은 새 clone에서 재시도합니다. 실제 Vercel manifest와 참조 payload의 내용·해시가 일치해야 워커가 작업을 완료 확인합니다.

읽기 실패 자료의 기존 계약과 완료된 과거 계약을 보존합니다. 새 보고서의 누락만으로 계약을 삭제하지 않으며, 모델이 명시적 변경·취소 근거를 확인한 경우에만 수정·철회합니다. 미공개 공시 일당은 `0`, 백로그 환산액은 `interpretation`에 별도 보관합니다.

## 계약표 중심 화면

첫 화면과 회사별 화면은 계약 바를 우선 표시합니다. 바를 클릭하거나 키보드로 선택하면 기간·고객·지역·공개된 일당과 뉴스 출처를 확인할 수 있습니다. 월 단위 날짜는 월 단위로 표시하고, 완료된 과거 계약은 현재 타임라인에 그리지 않습니다. 시장 분석 차트는 계약표 아래에서 펼쳐볼 수 있습니다.

계약표를 처음 열거나 회사를 바꾸면 현재 분기부터 보이도록 가로 스크롤 위치를 맞춥니다. 이전 분기도 왼쪽으로 스크롤해 확인할 수 있으며, 계약의 원래 날짜는 바꾸지 않습니다. 분기는 브라우저의 현재 날짜로 계산합니다(10–12월은 Q4).

**뉴스·공시** 탭(`#news`)에는 미확정 조건과 원문 근거를 보관합니다. 각 항목은 기본적으로 접혀 있으며, canonical 계약표에 반영한 발표는 접힌 **반영 이력**으로 이동합니다. 내부 모델명·설정·대조 과정은 화면에 노출하지 않고 증거 원장에 보관합니다.

### 계약 상태와 백로그 환산 요율

- `utils/newsEconomics.ts`는 모델이 남긴 `interpretation`을 화면 값으로 투영합니다. 문장에서 날짜·금액·기간을 다시 해석하지 않습니다.
- 같은 기본 수주의 금액과 작업 기간이 모두 확인되면 `계약 가치 ÷ 기간(일)`을 **$…k**로 바 안에 바로 표시합니다. '추정' 꼬리표는 붙이지 않고 선택 상세에서 공시값/백로그 환산값을 구분합니다. 1년=365일, 1개월=365/12일 가정은 선택 상세에 명시합니다. 최대 기간만 공개된 경우에는 단일 요율을 계산하지 않습니다.
- 추가 서비스·이동 비용 포함/제외, 계산식, 원문은 바를 눌러 확인합니다. 이 값은 계약 가치의 일평균 환산액이며 공시 operating dayrate가 아닙니다. 미공개 비용을 임의로 빼지 않습니다.
- 연장 구간별 금액·대응 기간은 모델이 선택하고 각각 row로 보존합니다. 코드는 선택된 금액÷기간만 검산하며 합산 수주액을 선박마다 중복 배분하지 않습니다.
- 새 수주는 검증·적용된 canonical row를 기존 Fleet Status 바 스타일로 표시합니다. 모델이 날짜 배치와 상태를 결정하고 원문 정밀도·한국어 배치 근거를 보존합니다. LOA나 미행사 옵션을 Firm으로 승격하지 않습니다.
- 이벤트의 미적용 row에서 별도 계약 바를 만들지 않습니다. 적용 이벤트의 `readingDecision.contractId`와 `row.id`는 canonical 계약 ID를 가리킵니다.
- 정확한 일자가 없는 일정 범위와 백로그 환산 요율은 가동률·활성 상태·평균 공시 dayrate·기존 시장 통계에 포함하지 않습니다. 별도 Dayrates 탭에서는 아래 규칙에 따라 구별해서 표시합니다. 체결 여부와 원문 날짜 정밀도는 따로 보존하며, 표시 이름만 바꿔 미공개 요율을 공시값으로 기록하지 않습니다. 금액·기간이 없거나 인용이 모호한 경우에는 요율을 생성하지 않고 뉴스에 근거를 남깁니다.

### 세대별 Dayrates

`#dayrates` 탭은 6G·7G·7G+·8G를 개별 선택하는 검은 배경의 산점도입니다. 채운 파란 다이아몬드는 공시 일당, 빈 다이아몬드는 백로그 환산액입니다. 점의 면적은 기본 계약 구간 길이에 비례하며 4년에서 상한을 둡니다. 점을 클릭하거나 키보드로 선택하면 기간·요율·계산식·날짜 배치 근거·원문 링크를 표시합니다. 원천 데이터 갱신 시 차트도 같은 투영 함수로 다시 계산합니다.

- `utils/dayrateTrends.ts`는 UI와 분리된 데이터 투영·통계 모듈입니다. 검증된 Firm 구간만 사용하고, 옵션·조건부 수주·미공개 요율·개별 날짜가 없는 연장 요율은 제외합니다. 원본 fleet/events를 수정하지 않습니다.
- 가로축은 **계약 구간 시작일**이며 수주 발표일이 아닙니다. 미래 개시 계약을 포함하고, 월·분기 단위 개시는 기존 Fleet Status의 배치 기준을 따릅니다. 현재 확보한 계약 구간이지 2021년 이후 전체 수주 이력이나 추가 서비스 제외로 보정한 시장 지수는 아닙니다. 장기 계약의 최신 공시 일당은 최초 개시 당시의 일당을 뜻하지 않습니다. 참고 이미지의 99건 원자료는 포함되어 있지 않습니다.
- 기본값은 7G, 백로그 환산 포함입니다. 체크를 끄면 공시 일당만 남습니다. 선택값은 `dayrateGen`, `dayrateBasis` URL query에 저장하여 새로고침·링크 공유 시 유지합니다.
- 7G+는 2025년 1월 1일 이후 시작한 계약 구간만 표시합니다. 가로축은 2025년부터, 표본 수·LOESS·음영도 같은 구간만 사용합니다. 다른 세대의 범위와 원본 계약 이력은 그대로 유지합니다.
- 추세선은 [d3-regression](https://github.com/HarryStevens/d3-regression)의 robust LOESS를 사용합니다(2.2.0 고정, lockfile로 재현). 기본 span 0.30, 소표본은 최소 4개 이웃을 확보하도록 span을 늘립니다. 5구간 또는 서로 다른 시작일 4개 미만이면 선과 밴드를 숨깁니다. 관측 구간 밖으로 외삽하지 않습니다.
- 음영은 인접 계약 구간의 LOESS 잔차에 대한 ±1 **표본 표준편차**입니다. 신뢰구간·예측구간이 아닙니다. 각 구간을 동일 가중치로 계산하고 계약 기간은 점 크기에만 반영합니다. [LOESS 방법 설명(NIST)](https://www.itl.nist.gov/div898/handbook/pmd/section1/pmd144.htm).
- `tests/dayrateTrends.test.cjs`는 실제 데이터의 세대별 표본·출처·환산 근거, 제외 규칙, 소표본, 선형/상수/잡음 데이터의 통계 계산, 점 면적, 축 범위를 검증합니다.

## Codex 원문 읽기와 실행기 복원

이 채팅을 깨우는 예약 작업은 사용하지 않습니다. macOS 로그인 사용자 세션·인터넷·ChatGPT로 인증된 Codex CLI가 필요합니다. API 키로 대체하지 않습니다. 다음 운영 명령은 실제 모델 호출·push·launchd 변경을 수행하므로 운영 전환 단계에서 실행합니다.

```bash
codex login
.venv/bin/python -m automation.install_news_worker
.venv/bin/python -m automation.news_worker
.venv/bin/python -m automation.update_news
```

전환 순서는 구 실행기 중지 → 호환 v1/v2 실행기 설치 및 `launchctl print` 확인 → 저장소 Actions 변수 `FLEET_READER_V2_ENABLED=true` → 서버 수집 → 마지막 정상 데이터 위의 복구 읽기 → 실제 배포 hash/payload 확인입니다. 변수 활성화 전에는 서버 workflow가 v2 수집을 실행하지 않습니다. 새 정책의 첫 수집에서는 같은 기존 보고서도 한 번 읽습니다.

```bash
launchctl bootout gui/$(id -u)/com.contiloop.drillship-news-worker
launchctl print gui/$(id -u)/com.contiloop.drillship-news-worker
```

- `reading_pipeline.py`: 서버 발견/큐와 로컬 읽기/적용 use case. `data/provenance/readings.json`의 작업 상태는 `pending`, `failed`, `completed`이며, completed는 적용·변경 없음·보류를 포함합니다. 같은 원문·정책·실질 비교 맥락이면 모델 호출과 계약 추가가 모두 0입니다. 적용 후의 자기 출력은 ack 맥락으로 기록하여 재큐 순환을 막습니다. 실제 `contextSha256`/`modelInputContextSha256`와 재큐 인과관계인 `comparisonEpochSha256`를 구분합니다.
- 원장은 원본 `documentSha256`와 모델 입력 `sourceSha256`를 구분합니다. macOS/Ubuntu의 PNG·OCR 차이를 원문 수정으로 오인하지 않습니다. 정책 문자열과 해시, 입력 baseline/선행 근거, 입력 revision, 출력, 인용, 변경 전 값, 읽기·적용·실패 시각을 보존합니다. 공통 원문 근거와 baseline은 내용 해시 객체로 한 번 저장하고 각 context는 객체/선행 job 참조로 재구성·검산합니다. PDF 이미지 base64는 공개 원장에 복사하지 않습니다.
- `source_materials.py`: 모든 PDF 페이지의 144 DPI PNG와 native/OCR 텍스트, 공식 SEC HTML 및 전체 뉴스 본문을 수집합니다. HTTP·본문 읽기 오류는 마지막 성공 시각과 분리하여 남기고 다음 수집에서 재시도합니다. 403은 실패로 보존하며 접근 가능한 같은 발행사의 공식 대체 자료를 별도로 수집합니다.
- `reading_service.py`/`codex_reader.py`: 실제 추출 버전을 포함하는 읽기 정책과 격리된 read-only Codex 어댑터. 형식 교정은 최대 3회입니다. 자료의 본문·URL·작업 ID는 명령이 될 수 없습니다.
- `review_news.py`는 이름을 호환 유지하지만 보고서·SEC HTML·뉴스를 모두 처리합니다. `news_review.py`, 결정론적 보고서 파서와 `news_jobs.py`의 v1 helper는 과거 fixture 회귀 확인용입니다. 구 뉴스·관측 원장은 비교 근거로만 읽으며 운영 쓰기 경로에서 재해석하지 않습니다.
- 성공 작업은 먼저 보존·게시하고 기술 실패만 5분~6시간 백오프로 재시도합니다. 처리 중 원본 bytes가 바뀌면 옛 결과를 적용하지 않고 새 버전을 대기시킵니다. 원문을 읽지 못했거나 reportDate를 확인하지 못하면 기존 값과 확인 불가 진단을 유지합니다.
- `update_news.py`: 단일 실행 잠금, 임시 clone, 허용 JSON 경로·symlink 검사, 최신 main 재확인, force push 금지, 실제 배포 payload 검증을 담당합니다. `.cache/readings-recovery.json`은 중단·경합·배포 실패 후 모델 결과 재사용용 로컬 체크포인트이며 Git에 올리지 않습니다.
- `news_worker.py`: v1/v2 큐를 읽습니다. v2는 전체 큐 fingerprint로 완료를 추정하지 않고 배포 확인된 개별 job ID만 체크포인트에 기록합니다. 작업이 없으면 clone·수집·모델 호출 없이 종료합니다.

실행기 상태·로그는 `.cache/news-worker-state.json`, `.cache/news-worker.log`, `.cache/news-worker-error.log`에 있습니다. 진단은 공개 `readingStatus`의 마지막 수집/읽기/적용 시각, collectionErrors/readingErrors와 원장의 job `lastError`·`nextAttemptAfter`를 비교합니다. Mac이 꺼지거나 로그인·네트워크가 없으면 큐가 남아 다음 정상 실행에서 이어집니다. 복구할 때 Git의 원장을 삭제하지 말고 인증·네트워크·OCR 설치 상태를 먼저 복구합니다. 인증 파일과 API 키를 GitHub/공개 데이터에 넣지 않습니다.

## 공식 원천

- [Transocean Fleet Status Report](https://www.deepwater.com/investors/fleet-status-report)
- [Valaris Investors / Fleet Status Report](https://www.valaris.com/investors/) (SEC submissions CIK 0000314808도 우선 확인)
- [Noble Fleet Status Report](https://noblecorp.com/our-investors/reports-filings/fleet-status-report/)
- [Seadrill Fleet](https://www.seadrill.com/fleet/)

한국 DART 공시는 사용하지 않습니다.

## 로컬 실행

```bash
brew install python@3.12 node@22 tesseract
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r automation/requirements.lock
npm ci
npm run data:check
npm run dev
```

서버 수집·큐 산출물을 만들고 로컬 검증하려면:

```bash
npm run data:sync
npm run verify
```

## 생성 산출물

- `public/data/manifest.json`: 현재 데이터 해시, 보고서 기준일, 공식 출처
- `public/data/fleet.<hash>.json`: 62척 canonical fleet data
- `public/data/events.<hash>.json`: 자동 반영하지 않은 공식 뉴스/공시와 모델 판단 이력
- `public/data/changes.json`: 직전 데이터와의 semantic diff 요약
- `data/provenance/sources.json`: 문서 URL, 원본/추출 SHA-256, 수집 상태
- `data/provenance/readings.json`: 작업 원장·정책·맥락·출력·변경 전 이력
- `data/provenance/observations.json`: 과거 관측 evidence (비교용 보존)
- `automation/tests/fixtures/reports/`: CI에서도 실행되는 현재 공식 보고서 golden fixtures

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
.venv/bin/python -m automation.fleet_sync --check   # collect + queue, no publication
.venv/bin/python -m automation.fleet_sync --write   # server metadata + last good fleet
.venv/bin/python -m pytest automation/tests -q
npm run build
```

`npm run build`는 TypeScript 검사와 프로덕션 번들 생성을 함께 수행합니다. 공통 스키마·참조 무결성 또는 빌드가 깨지면 게시를 중단하고 마지막 정상 사이트를 유지합니다. `npm run verify`도 프로젝트 `.venv/bin/python`을 사용합니다. Python 패키지는 hash 잠금 `automation/requirements.lock`, Node 패키지는 `package-lock.json`으로 복원합니다. Ubuntu에서는 `tesseract-ocr tesseract-ocr-eng`가 필요하며 workflow가 설치합니다.
