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

모호한 뉴스, LOA, 정확한 날짜가 없는 option은 계약으로 추정하지 않습니다. `public/data/events.<hash>.json`에 별도 공지로 남기며, 기존 검증 데이터를 덮어쓰지 않습니다. 공개되지 않은 dayrate는 기존 UI 호환을 위해 `0`으로 직렬화하되 provenance에는 `undisclosed`로 기록합니다. 6시간 주기의 공식 원천 polling이며 스트리밍 실시간 피드는 아닙니다.

## 계약표 중심 화면

첫 화면과 회사별 화면은 계약 바를 우선 표시합니다. 바를 클릭하거나 키보드로 선택하면 기간·고객·지역·공개된 일당과 뉴스 출처를 확인할 수 있습니다. 월 단위 날짜는 월 단위로 표시하고, 완료된 과거 계약은 현재 타임라인에 그리지 않습니다. 시장 분석 차트는 계약표 아래에서 펼쳐볼 수 있습니다.

**뉴스·공시** 탭(`#news`)에는 계약표에 아직 넣을 수 없는 조건만 기본 표시합니다. 분기·반기 시작만 공개된 수주, LOA 등은 날짜를 추정하지 않고 별도로 보존합니다. 각 항목은 기본적으로 접혀 있으며, 계약표에 반영한 발표는 접힌 **반영 이력**으로 이동합니다. 내부 모델명·설정·대조 과정은 화면에 노출하지 않고 증거 원장에 보관합니다.

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
- 미공개 일당을 계약 가치÷기간으로 채우지 않습니다. 분기·반기 시작을 특정 날짜로 바꾸지 않습니다. 기존 옵션 행사와 새 수주를 구별하고, 합산 수주액을 개별 선박에 중복 배정하지 않습니다.

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
- total contract value를 기간으로 나누어 dayrate를 추정하지 않습니다.
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
