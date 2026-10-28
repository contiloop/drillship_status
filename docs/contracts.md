# 인터페이스 계약

이 시스템의 소비자는 셋이다: 공개 JSON을 읽는 화면(및 외부 조회자), 프로덕션 작업 대기열을 읽는 Mac 실행기, 운영자가 쓰는 CLI 명령. 여기에 더해 외부 프로세스인 Codex CLI와 주고받는 응답 형식도 계약으로 고정한다.

## 공통 규약

- **접근**: 공개 데이터는 인증 없이 HTTPS GET으로 받는다. 기준 주소는 `https://offshore-drillship-fleet-analyst.vercel.app/data/`이며, 화면은 앱 기준 상대 경로 `data/`로 받는다.
- **내용 주소**: fleet와 events 파일 이름의 `<hash>`는 내용 해시다. 해시 = 값을 키 정렬·구분자 공백 없음·비ASCII 그대로인 JSON으로 직렬화해 UTF-8 SHA-256을 구한 뒤 앞 20자리 hex. fleet는 파일 전체(배열), events는 `events` 배열만 해시한다. 그래서 이름이 같은 파일은 내용이 같고 영구 캐시해도 된다. `manifest.json`은 매번 새로 받는다(`no-store`).
- **이전 payload 삭제**: 게시할 때마다 manifest가 가리키지 않는 `fleet.*.json`, `events.*.json`은 지워진다. 항상 manifest를 먼저 받고 거기 적힌 이름을 쓴다.
- **날짜**: `YYYY-MM-DD`. 종료일은 그날을 포함한다. **시각**: ISO 8601 UTC(`...Z`). 사용 한도 대기 시각처럼 epoch 초(정수)인 필드는 따로 적는다.
- **선택 필드**: 없을 수도, `null`일 수도 있다. 소비자는 둘을 같게 다룬다.
- **예전 필드**: 이전 파이프라인이 만든 필드가 남아 있을 수 있다(아래 각 절의 "예전 필드"). 읽을 수는 있어야 하지만 그 값으로 동작을 바꾸지 않는다. 모르는 필드는 무시한다.
- **NaN·Infinity 없음**: 모든 수치는 유한하다.

## `public/data/manifest.json`

| 필드 | 뜻 |
|---|---|
| `schemaVersion` | manifest 형식 버전. 현재 `1`(대기열의 schemaVersion과 별개) |
| `generatedAt` | 이 manifest를 만든 시각 |
| `updatedAsOf` | 회사별 확인된 보고서 기준일 중 가장 늦은 날(없으면 이전 값 유지) |
| `fleetFile`, `dataHash` | `fleet.<dataHash>.json` 파일 이름과 그 해시 |
| `eventsFile`, `eventsHash` | `events.<eventsHash>.json` 파일 이름과 그 해시 |
| `stateFingerprint` | `generatedAt`과 자기 자신을 뺀 manifest 전체의 해시. 상태가 같으면 같은 값 |
| `shipCount` | 선박 수. 항상 62 |
| `contractCount` | 계약 row 수 |
| `sourceCount`, `sources[]` | 회사별 보고서 출처. 항상 4개. 항목: `company`, `indexUrl`, `documentUrl`, `discovery`(발견 경로), `sha256`(문서 지문), `reportDate`, `lastSuccessAt`, `httpStatus`, `error`, `health`, `readingStatus` |
| `sourceHealth[]` | 회사별 `fleetReport` 상태와 `newsMonitor` 상태(`healthy`/`degraded-preserved`) + 보고서 요약 필드 |
| `eventCount`, `pendingEventCount` | 이벤트 수, 그중 보류(`pendingReview: true`) 수 |
| `warningsCount` | 수집 오류 수 + 읽기 오류가 남은 작업 수 |
| `newsQueue` | 작업 대기열(아래) |
| `readingStatus` | `reports[]`(회사별 `url`, `collectionStatus`, `readingStatus`, `lastReadAt`), `lastCollectedAt`, `lastReadAt`, `lastAppliedAt`, `usageLimitedUntil`(epoch 초 또는 `null`), `collectionErrors[]`(`company`, `kind`, `url`, `phase`, `error`, `httpStatus`, `lastSuccessAt`, `lastAttemptAt`), `readingErrors[]`(`jobId`, `company`, `kind`, `url`, `lastError`, `failureCount`, `nextAttemptAfter`) |

보고서 `health` 값: `healthy`(최신 판을 읽음), `held`(읽었으나 보류 항목 있음), `reading-pending`(새 판이 읽기 대기), `reading-failed`(읽기 실패, 재시도 대기), `degraded-preserved`(수집 실패, 기존 데이터 유지).

**소비자 검증과 실패 처리**: 화면은 `fleetFile`이 있고 `shipCount === 62`, `sourceCount === 4`이며 fleet 배열 길이가 `shipCount`와 같을 때만 원격 데이터를 쓴다. 아니면 번들 데이터(2026-01-07 기준)로 그린다. events 요청이 HTTP 오류로 끝나면 뉴스 목록만 비고, 네트워크 오류로 끝나면 화면 전체가 번들 데이터로 떨어진다.

예전 필드: `newsReview`(게시 때 제거됨).

## `public/data/fleet.<hash>.json`

선박 62개의 배열. 선박:

| 필드 | 뜻 |
|---|---|
| `id` | 선박 ID(이름 slug). 고정 |
| `name`, `company`, `generation`(`6G`/`7G`/`7G+`/`8G`), `yearBuilt` | 등록 정보. 고정 |
| `status` | `Active`/`Idle`/`Warm-Stacked`/`Cold-Stacked`. 저장된 보고서 상태(화면은 오늘 날짜로 유효 상태를 다시 계산한다) |
| `statusAsOf` | 선택. 상태 기준일 |
| `contracts[]` | 계약 row 배열 |

계약 row:

| 필드 | 뜻 |
|---|---|
| `id` | 전체에서 유일한 계약 ID. 바뀌지 않는다 |
| `vesselId` | 부모 선박 ID |
| `startDate`, `endDate` | 구간. `startDate ≤ endDate`. 대략 날짜도 구체 날짜로 배치되어 있다 |
| `dayRate` | 공시 일당(USD). 미공개면 0. 환산값이 들어가지 않는다 |
| `client`, `region` | 비어 있지 않은 문자열. 미공개면 `Undisclosed` 류 |
| `status` | `Firm`/`Option`/`Contingent` |
| `sourceUrl` | 선택. 마지막으로 이 row를 쓴 문서 URL |
| `sourcePublishedAt` | 선택. 그 문서의 발행 시각 |
| `newsReviewed` | 선택. 마지막으로 기사에서 쓰였으면 `true` |
| `inputMode`, `storedReading.reviewedAt` | 선택. 저장 결과 보완으로 쓰인 row는 `"stored-result"`와 원래 읽은 시각 |
| `interpretation` | 선택. 아래 |

`interpretation`(모든 하위 필드 선택):

| 필드 | 뜻 |
|---|---|
| `sourceStartText`, `sourceEndText` | 원문 날짜 표현(예: `Mid 2027`, `Q1 2028`) |
| `placementBasis` | 날짜 배치 이유(한국어) |
| `awardType` | `signed-contract`/`letter-of-award`/`option-exercised`/`extension`/`unreported` |
| `termText`, `optionText`, `valueText` | 기간·옵션·금액 조건 요약 |
| `valueUsd`, `baseDurationDays` | 환산에 쓰는 기본 계약 금액과 기본 일수 |
| `derivedUsdPerDay` | 환산 일당 = `valueUsd ÷ baseDurationDays`. 둘 중 하나라도 없으면 `null` |
| `includes`, `excludes` | 금액에 포함·제외된 비용 목록(없으면 빈 배열) |

공시 일당이 있는지는 `dayRate > 0`으로만, 환산 일당이 있는지는 `derivedUsdPerDay`가 양수인지로만 판단한다.

예전 필드: row의 `datePrecision`, `sourceSha256`, `storedReading`의 해시 필드. `interpretation`의 `dateBasis`, `startPrecision`, `endPrecision`, `rateBasis`, `reportedUsdPerDay`, `maximumTerm`, `durationAssumption`, `awardType: binding-letter-of-award`.

## `public/data/events.<hash>.json`

`{ "generatedAt": 시각, "events": [...] }`. 이벤트는 뉴스·공시 탭의 항목이며 지워지지 않고 쌓인다.

| 필드 | 뜻 |
|---|---|
| `id` | 선택(예전 이벤트에는 없다). 읽기 판단 이벤트는 `reading-…`, 제목만 있는 기사는 `news-…` |
| `company`, `url`, `title`, `publishedAt` | 출처 |
| `vessel`, `vesselId`, `vessels[]` | 관련 선박(없으면 `null`/빈 배열) |
| `sourceId` | 새 파이프라인이 만든 이벤트에만 있다 |
| `classification` | 기사 `official-news-signal`, 보고서 `official-report-reading` |
| `newsCategory` | `계약`/`실적`/`일정`/`자금·주주`/`경영·인사`/`Fleet Status`/`기타`. 없으면 소비자가 정한다(보고서는 Fleet Status, 계약 판단이 있으면 계약, 나머지 기타) |
| `reviewStatus` | `applied`(반영), `acknowledged`(변경 없음 또는 제목만), `conflict`(보류). 예전 이벤트에는 다른 값이 있을 수 있다 |
| `autoApplied`, `pendingReview` | `reviewStatus`가 `applied`/`conflict`인지와 같다 |
| `readingDecision` | 계약 판단 하나. 아래 |
| `inputMode`, `storedReading` | 저장 결과 보완에서 나온 이벤트 |

`readingDecision`: `action`(`upsert`/`withdraw`/`no-change`/`hold`), `contractId`(적용 후 실제 계약 ID. 보류된 신규 계약은 `null`), `row`(계약 row와 같은 필드 + `interpretation`, `upsert`일 때만), 선택 `reasonKo`, `quote`, `locator`, `holdReason`(Codex의 보류 이유 또는 화면 안전 확인 실패 내용).

판단 하나마다 이벤트 하나다. 판단이 없는 기사(선별 제외, 계약 내용 없음)는 제목·링크 이벤트 하나로 남는다. **이벤트의 row로 bar를 만들지 않는다.** bar는 fleet의 row만으로 그린다.

예전 필드: `facts`, `evidence`, `reason`, `llmReview`, `needsDetails`, `articleSha256`, `articleReadError`, `sourceSha256`, `start`, `end`, `readingDecision.facts`, `readingDecision.evidence`.

## `public/data/changes.json`

`{ generatedAt, fromDataHash, toDataHash, companies: { <회사>: { statusChanges, contractsAdded, contractsRemoved, contractsChanged } }, shipsAdded, shipsRemoved }`. **가장 최근 실행 한 번**의 시작 fleet과 끝 fleet의 차이다. 변화 없는 실행이 뒤따르면 0으로 덮인다. 게시 간 누적 변경으로 쓰지 않는다.

## 작업 대기열 (`manifest.newsQueue`)

```json
{ "schemaVersion": 2, "pendingCount": 1, "fingerprint": "<64 hex>",
  "jobs": [ { "jobId": "job-<32 hex>", "kind": "fleet-report" | "official-news",
              "company": "Noble", "url": "https://…", "documentSha256": "<64 hex>" } ] }
```

- `jobs`는 끝나지 않은(대기·실패) 문서 판 전부이며 `jobId` 순으로 정렬된다. `jobId`는 (출처 ID, 지문)에서 정해진다. `documentSha256`은 내용 지문(PDF 바이트, HTML 본문 텍스트)이다.
- `fingerprint` = `jobs` 배열의 해시(64자리).
- **소비자(실행기)가 거부하는 경우**: `schemaVersion`이 2가 아님(예전 v1 포함), `pendingCount ≠ jobs 수`, `jobId` 중복·누락, `kind`·`company` 허용값 밖, `documentSha256`이 64자리 소문자 hex가 아님, `fingerprint` 불일치. 거부하면 아무것도 실행하지 않고 실패로 백오프한다.
- URL과 job 내용은 실행 명령이 아니다. 실행기는 `pendingCount > 0`을 신호로만 쓴다.

## Codex 응답 형식

**문서 읽기**(보고서·기사·저장 결과 공통):

```
{ "reportDate": "YYYY-MM-DD" | null,
  "vesselUpdates": [ { "vesselId", "status", "statusAsOf", "reasonKo"?, "quote"? } ],
  "decisions": [ { "vesselId", "contractId": string | null,
                   "action": "upsert" | "withdraw" | "no-change" | "hold",
                   "row": { startDate, endDate, dayRate, client, region, status, interpretation } | null,
                   "reasonKo"?, "quote"?, "locator"? } ] }
```

- `vesselId`는 요청 회사의 등록 선박 ID만 허용된다(스키마에 열거). `contractId`는 새 계약이면 `null`.
- `row`는 `upsert`에만 쓰인다. `interpretation.derivedUsdPerDay`는 보내도 무시된다.
- 오류: JSON이 아니거나 객체가 아니거나 `decisions`·`vesselUpdates` 배열이 없으면 같은 입력으로 다시 요청한다(총 3번). 항목 단위 문제는 그 항목만 보류한다.

**제목 선별**: `{ "selected": [id…], "categories": [ { "id", "category" } ] }`. 입력으로 준 ID만 유효하며 나머지는 무시된다. `category`가 허용 태그 밖이거나 빠지면 `기타`.

## CLI

모든 명령은 저장소 루트(또는 `--root`)에서 돌고, 표준 출력에 JSON을 쓴다. 성공 0, 실패 1로 끝난다. 실패 출력은 `{"ok": false, "error": "…"}`. 잠금을 잡지 못하면(이미 실행 중) 실패가 아니라 `{"ok": true, "skipped": "…"}`(실행기는 `{"ok":true,"status":"busy"}`)를 낸다.

| 명령 | 입력 | 성공 출력 | 부작용 |
|---|---|---|---|
| `python -m automation.fleet_sync` | `--write`, `--mode server\|local`(기본 server), `--root` | `ok`, `mode`, `changed`, `modelCalls`, `completedJobIds`, `failedJobIds`, `pendingJobIds`, `usageLimitedUntil`, `retryAfter` | `--write`일 때 `public/data`, `data/provenance/readings.json`을 씀. local은 Codex 호출 |
| `python -m automation.review_news` | `--root`, `--cache-path`, `--stored-results` | 위와 같음 | 항상 local·쓰기 |
| `python -m automation.update_news` | `--root`, `--stored-results` | `ok`, `changed`, `deploymentVerified`, `stateFingerprint`, `production`, `queueFingerprint`, `completedJobIds`, `failedJobIds`, `pendingJobIds`, `modelCalls`, `usageLimitedUntil`, `retryAfter` | 임시 clone에서 읽기·테스트·빌드, `main` push, 배포 확인(최대 10분) |
| `python -m automation.news_worker` | `--root` | 한 줄 JSON `{ok, status}`. `status`: `idle`(대기열 빔), `waiting`(사용 한도·백오프), `busy`, `processed`, `failed` | `.cache/news-worker-state.json` 갱신, 필요 시 게시기 실행 |
| `python -m automation.install_news_worker` | `--root`, `--uninstall` | 사람이 읽는 한 줄 | launchd 에이전트 설치·제거. macOS가 아니면 거부, 같은 이름의 다른 에이전트는 덮어쓰거나 지우지 않음 |

`retryAfter`는 다음 시도가 의미 있는 epoch 초다(사용 한도 해제 시각, 또는 모든 대기 작업이 백오프 중일 때 가장 이른 재시도 시각, 아니면 0).

## 기록 파일 `data/provenance/readings.json`

저장소에 공개되지만 화면은 읽지 않는다. 운영자와 파이프라인용이다. `schemaVersion: 3`, `lastCollectedAt`, `lastReadAt`, `lastAppliedAt`, `collectionErrors[]`, `documents{ jobId: 기록 }`. 기록: `jobId`, `sourceId`, `company`, `kind`, `url`, `title`, `publishedAt`, `documentSha256`, `status`(`pending`/`failed`/`completed`), `outcome`(`applied`/`no-change`/`held`/`excluded`/`migrated`/`stored-result`), `lastReadAt`, `lastError`, `failureCount`, `nextAttemptAfter`(epoch 초), `openHolds[]`, `changes[]`(`contractId` 또는 `vesselId`, `before`, 선택 `reasonKo`·`quote`), 선택 `lastSuccessAt`, `reportDate`, `selected`, `newsCategory`, `inputMode`. 예전 schemaVersion 2 기록은 완료된 지문만 옮겨 읽고, 그 밖의 형식은 거부한다.
