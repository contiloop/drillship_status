# Drillship Status

Transocean, Valaris, Noble, Seadrill의 공식 fleet status report와 공식 IR/SEC 공지를 추적하는 드릴십 대시보드입니다.

프로덕션: <https://offshore-drillship-fleet-analyst.vercel.app>

## 자동 갱신 흐름

1. GitHub Actions가 6시간마다 각 회사의 공식 보고서 인덱스와 IR 뉴스 페이지를 확인하고, Valaris는 공식 인덱스 장애 시 SEC 제출 자료도 검증된 대체 원천으로 확인합니다.
2. 최신 PDF 또는 SEC HTML의 원문 형식, 파일 크기, 해시, 보고서 날짜를 검증합니다.
3. 회사별 결정론적 파서가 선박·계약·dayrate·상태를 추출합니다.
4. 62척 master registry, 날짜, enum, 계약 ID, 출처 추적성, 보고서 rollback을 검증합니다.
5. 검증을 모두 통과한 경우에만 `public/data/manifest.json`과 content-addressed fleet JSON을 커밋합니다.
6. 기존 Vercel Git 연동이 `main`의 새 커밋을 프로덕션으로 배포합니다.

모호한 뉴스, LOA, 정확한 날짜가 없는 option은 canonical 확정 계약으로 승격하지 않습니다. `public/data/events.<hash>.json`에 근거를 남기며, 기존 검증 데이터를 덮어쓰지 않습니다. 공개되지 않은 dayrate는 기존 UI 호환을 위해 `0`으로 직렬화하되 provenance에는 `undisclosed`로 기록합니다. 화면의 백로그 환산 요율은 이 값과 별도로 계산합니다. 6시간 주기의 공식 원천 polling이며 스트리밍 실시간 피드는 아닙니다.

## 계약표 중심 화면

첫 화면과 회사별 화면은 계약 바를 우선 표시합니다. 바를 클릭하거나 키보드로 선택하면 기간·고객·지역·공개된 일당과 뉴스 출처를 확인할 수 있습니다. 월 단위 날짜는 월 단위로 표시하고, 완료된 과거 계약은 현재 타임라인에 그리지 않습니다. 시장 분석 차트는 계약표 아래에서 펼쳐볼 수 있습니다.

계약표를 처음 열거나 회사를 바꾸면 현재 분기부터 보이도록 가로 스크롤 위치를 맞춥니다. 이전 분기도 왼쪽으로 스크롤해 확인할 수 있으며, 계약의 원래 날짜는 바꾸지 않습니다. 분기는 브라우저의 현재 날짜로 계산합니다(10–12월은 Q4).

**뉴스·공시** 탭(`#news`)에는 미확정 조건과 원문 근거를 보관합니다. 각 항목은 기본적으로 접혀 있으며, canonical 계약표에 반영한 발표는 접힌 **반영 이력**으로 이동합니다. 내부 모델명·설정·대조 과정은 화면에 노출하지 않고 증거 원장에 보관합니다.

### 계약 상태와 백로그 환산 요율

- `utils/newsEconomics.ts`는 검증된 뉴스의 선박별 직접 인용을 화면 전용 모델로 투영합니다. 새 뉴스가 갱신되면 같은 규칙이 자동 적용됩니다. 모델 설정이나 canonical 데이터는 바꾸지 않습니다.
- 같은 기본 수주의 금액과 작업 기간이 모두 확인되면 `계약 가치 ÷ 기간(일)`을 **$…k**로 바 안에 바로 표시합니다. '추정' 꼬리표는 붙이지 않고 선택 상세에서 공시값/백로그 환산값을 구분합니다. 1년=365일, 1개월=365/12일 가정은 선택 상세에 명시합니다. 최대 기간만 공개된 경우에는 단일 요율을 계산하지 않습니다.
- 추가 서비스·이동 비용 포함/제외, 계산식, 원문은 바를 눌러 확인합니다. 이 값은 계약 가치의 일평균 환산액이며 공시 operating dayrate가 아닙니다. 미공개 비용을 임의로 빼지 않습니다.
- 연장 수주 여러 건은 같은 인용 문장에 묶인 금액·기간 쌍으로 각각 계산합니다. 기존 계약 전체 기간으로 나누거나 별개 요율을 평균내지 않습니다. West Capella는 연장 120일/$41m, 120일/$53m을 별도로 표시합니다.
- 새 수주도 기존 계약과 같은 줄·높이·상태별 색상으로 표시하며, 바 안에는 **고객명 · $요율**만 표시합니다. 체결 계약은 기존 Firm 스타일, LOA는 기존 Contingent 스타일을 사용합니다. 분기·반기·연도 개시 예정만 공개된 경우 해당 구간 첫날을 배치 기준으로 잡고, 기본 계약 기간만큼만 그려 종료일을 하나로 표시합니다. 원문에 직접 연속 작업 조건이 있으면 앞선 Firm 계약 종료 다음 날과 공개 개시 구간 첫날 중 늦은 날부터 배치합니다. 이 날짜가 공개 개시 구간을 벗어나면 임의로 이동시키지 않습니다. 원문의 개시 정밀도와 날짜 배치 기준은 선택 상세에 보존하며 공시된 정확한 날짜로 취급하지 않습니다. 미행사 옵션 기간은 합산하지 않습니다.
- 해당 발표가 이미 canonical row에 반영되었으면 중복 예상 바를 만들지 않습니다. 기사 URL·선박이 일치하는 단일 row에만 경제 조건을 연결합니다. 선박명만 보고 다른 계약에 요율을 붙이지 않습니다.
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

## Codex 뉴스 원문 대조 (API 아님)

GitHub Actions가 6시간마다 수집하며, 원문 해시·기존 계약·이전 발표·읽기 지침이 바뀐 기사만 `manifest.newsQueue`에 등록합니다. HTTP 오류는 다음 서버 수집에서 재시도하며 LLM 작업을 만들지 않습니다. **이 채팅을 주기적으로 깨우는 Codex 예약 실행은 사용하지 않습니다.**

Mac의 launchd 실행기는 공개 manifest의 대기 작업 유무만 5분마다 확인합니다. 작업이 없으면 Git clone·원문 수집·모델 호출 없이 종료합니다. 새 작업이 있으면 아래 게시 명령을 실행합니다. Mac이 잠들거나 꺼진 동안 작업은 서버에 남고, 다시 실행 가능해지면 처리됩니다. **Codex 앱 창은 필요 없고, 로그인된 사용자 세션·인터넷·Codex CLI 인증이 필요합니다.**

```bash
codex login                         # ChatGPT 계정 로그인 (API key 로그인 불가)
.venv/bin/python -m automation.update_news
.venv/bin/python -m automation.install_news_worker  # 로그인 사용자 launchd에 설치
.venv/bin/python -m automation.news_worker          # 서버 대기 작업 1회 확인
```

재설치 시 Python 3.12, Node 22, Codex CLI 0.162.1 이상을 설치하고 아래 로컬 실행 절차로 의존성을 복원한 뒤 `codex login`, `automation.install_news_worker`를 실행합니다. 인증 파일을 서버나 GitHub secret으로 복사하지 않습니다. launchd 설정은 `~/Library/LaunchAgents/com.contiloop.drillship-news-worker.plist`, 상태와 로그는 프로젝트 `.cache/news-worker-*`에 저장합니다.

```bash
launchctl print gui/$(id -u)/com.contiloop.drillship-news-worker
launchctl bootout gui/$(id -u)/com.contiloop.drillship-news-worker  # 실행기 중지
```

- `codex_reader.py`: ChatGPT 인증 여부 확인, `gpt-6-luna`, `model_reasoning_effort=medium`, `service_tier=default`(Fast off)를 실행마다 명시. API 키 환경 변수 제거. 원문+기존 계약을 입력하며 도구/쓰기 권한 없는 읽기 전용 실행으로 구조화 JSON만 받습니다.
- `news_review.py`: 원문 SHA-256, 선박 registry, 원문 직접 인용, 날짜 정밀도, 일당 공개 여부를 검증하고 결정론적으로 대조합니다. 모델 출력이 임의 파일 편집이나 배포 명령이 될 수 없습니다.
- `data/news/reviews.json`: 기사별 Codex 해석·직접 인용·모델 설정·확인 시각을 보존합니다. 원문·기존 계약·이전 발표·읽기 지침이 같으면 재호출하지 않고, 변경되면 다시 읽습니다. 기사 시점보다 앞선 증거만 비교에 사용하여 자기 자신의 수정 내용을 다시 근거로 삼지 않습니다. 후속 FSR이 발표일을 넘으면 FSR을 우선합니다.
- `update_news.py`: 임시 clone에서 수집/대조/테스트/빌드 후 데이터 경로만 커밋·push하고 실제 Vercel manifest/payload를 확인합니다. 사용자의 작업 파일은 수정하지 않습니다. 동시 push 충돌은 강제 덮어쓰기 없이 실패하고 다음 실행에서 최신 main으로 재시도합니다.
- `news_jobs.py`: 서버와 로컬이 동일한 읽기 조건과 컨텍스트 해시를 공유합니다. 원문·비교 맥락이 같은 기사는 큐에 등록하지 않습니다.
- `news_worker.py`: 서버 큐가 비었으면 모델을 호출하지 않습니다. 처리한 큐는 중복 실행하지 않고, 실패는 5분부터 최대 6시간까지 지수 백오프로 재시도합니다. 서버 JSON은 실행 명령이 될 수 없으며, 게시기는 신뢰하는 저장소에서 원문을 다시 검증합니다.
- Transocean은 IR 호스트의 403을 우회 조작하지 않고 같은 발행사의 `https://www.deepwater.com/news` 공식 원문을 사용합니다.
- canonical 미공개 일당을 계약 가치÷기간으로 덮어쓰지 않습니다. 화면에서만 백로그 환산 요율과 기본 기간 길이의 계약 바를 제공합니다. 기존 옵션 행사와 새 수주를 구별하고, 합산 수주액을 개별 선박에 중복 배정하지 않습니다.

## 공식 원천

- [Transocean Fleet Status Report](https://www.deepwater.com/investors/fleet-status-report)
- [Valaris Investors / Fleet Status Report](https://www.valaris.com/investors/) (SEC submissions CIK 0000314808도 우선 확인)
- [Noble Fleet Status Report](https://noblecorp.com/our-investors/reports-filings/fleet-status-report/)
- [Seadrill Fleet](https://www.seadrill.com/fleet/)

한국 DART 공시는 사용하지 않습니다.

## 로컬 실행

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r automation/requirements.lock
npm ci
npm run data:check
npm run dev
```

검증된 데이터를 실제 생성하려면:

```bash
npm run data:sync
npm run verify
```

## 생성 산출물

- `public/data/manifest.json`: 현재 데이터 해시, 보고서 기준일, 공식 출처
- `public/data/fleet.<hash>.json`: 62척 canonical fleet data
- `public/data/events.<hash>.json`: 자동 반영하지 않은 공식 뉴스/공시 signal
- `public/data/changes.json`: 직전 데이터와의 semantic diff 요약
- `data/provenance/sources.json`: 문서 URL, SHA-256, parser version
- `data/provenance/observations.json`: 계약별 페이지/행 evidence
- `automation/tests/fixtures/reports/`: CI에서도 실행되는 현재 공식 보고서 golden fixtures

## 데이터 규칙

- 계약 상태: `Firm`, `Option`, `Contingent`
- 선박 상태: `Active`, `Idle`, `Warm-Stacked`, `Cold-Stacked`
- 월 정밀도 원문의 시작일은 월 1일, 종료일은 월 말로 투영합니다.
- Noble의 종료 token은 exclusive boundary이므로 바로 전날로 저장합니다.
- 경제적 가동률은 `Firm` 구간의 합집합으로 계산하여 같은 달 전환 계약의 겹침을 이중 계산하지 않습니다.
- total contract value÷기본 기간은 화면 전용 환산값으로 표시하고, 공시 dayrate 및 확정 일자 기준 통계와 구분합니다.
- 최신 snapshot에 없는 완료 계약은 이력 보존 규칙에 따라 유지하고, stale future 계약은 제거합니다.
- 선박의 원문 상태에는 `statusAsOf`를 함께 저장하며, 그 이후 시작한 현재 Firm 계약만 화면의 유효 상태를 Active로 전환합니다.

## 운영 명령

```bash
python3 -m automation.fleet_sync --check   # fetch + parse + validate, no write
python3 -m automation.fleet_sync --write   # validated generated artifacts only
python3 -m pytest automation/tests -q
npm run build
```

`npm run build`는 TypeScript 검사와 프로덕션 번들 생성을 함께 수행합니다. 파싱 또는 스키마가 달라지면 workflow는 실패하며 마지막 정상 데이터와 프로덕션 사이트는 그대로 유지됩니다.
