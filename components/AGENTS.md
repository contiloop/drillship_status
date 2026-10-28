# 화면 계층 — App.tsx, components/, utils/, types.ts, tests/

화면 코드는 저장소 루트에 흩어져 있어 이 파일이 그 전체를 다룬다: `App.tsx`, `index.tsx`, `index.css`, `components/`, `utils/`, `types.ts`, `tests/*.test.cjs`와 `tests/fixtures/`.

## 범위

이 계층이 가진 것:
- 공개 JSON 적재와 실패 시 내장 데이터 대체(`App.tsx`).
- Fleet Status 타임라인과 계약 상세(`FleetGantt.tsx`), Dayrates 산점도(`DayrateTrends.tsx`), 뉴스·공시 목록과 기사 카드(`Announcements.tsx`, `OfficialAnnouncementCard.tsx`), 시장 차트(`MarketChart.tsx`), 상단 통계 카드와 가동률(`App.tsx`).
- 순수 계산(`utils/`): 기간·표시 위치(`contractTimeline.ts`), 환산 일당 투영(`newsEconomics.ts`), Dayrates 관측치·LOESS·축(`dayrateTrends.ts`), 선박 유효 상태·Firm 일수 합집합(`shipStatus.ts`).
- 공개 데이터 타입(`types.ts`).

이 계층이 갖지 않는 것:
- 계약 데이터를 만들거나 고치는 일. `automation/`과 생성 데이터(`public/data`, `data/provenance`)를 이 계층의 작업으로 바꾸지 않는다.
- 자연어 해석. 기사 제목·본문·`termText` 같은 문장에서 금액·기간·분기·날짜를 뽑는 정규식을 두지 않는다. 화면에 필요한 값은 이미 계약 row의 필드에 있다.
- 환산 일당 계산. `interpretation.derivedUsdPerDay`를 그대로 쓴다.

## 불변식

- **bar는 fleet의 계약 row에서만 나온다.** `timelineContracts(ship)`는 row를 그대로 매핑하며, 이벤트의 `readingDecision.row`나 기사에서 bar를 추가·이동하지 않는다. 계약 row가 없는 기사는 bar를 만들지 않는다.
- **공시 일당**은 `reportedRate(row) > 0`(즉 `dayRate`가 유한한 양수)일 때만이다. **환산 일당**은 `interpretationEconomics`가 `derivedUsdPerDay`가 유한한 양수일 때만 만든다. 다른 필드(`rateBasis`, `reportedUsdPerDay` 등)로 판단하지 않는다.
- **통계 포함**은 `isStatisticalContract`(실제 ISO 날짜, 시작 ≤ 종료)로만 정한다. 원문 날짜가 대략적이었는지(`sourceStartText`, 예전 `dateBasis`·`startPrecision`)로 계약을 빼지 않는다.
- 평균 일당·시장 차트·현재 공시 일당은 `isCurrentReportedRate`(Firm, 오늘을 덮음, 공시 일당 > 0)만 쓴다. 환산 일당은 넣지 않는다.
- Dayrates 관측치는 Firm row 중 공시 또는 환산 일당이 있는 것만이다. 7G+는 2025-01-01 이후 시작 구간만 쓰고, 이 창을 추세 계산 **전에** 적용한다. 점 5개 미만이거나 서로 다른 시작일 4개 미만이면 추세선과 음영을 그리지 않는다.
- 선박 유효 상태(`getComputedStatus`): 저장 상태 `Active`인데 오늘 Firm 계약이 없으면 `Idle`. 비가동 상태는 `statusAsOf` 이후 시작한 Firm 계약이 오늘을 덮을 때만 `Active`.
- Economic 가동률의 Firm 일수는 선박별 구간 합집합이다(같은 달 경계를 공유하는 인접 계약을 이중 계산하지 않음).
- `types.ts`의 예전 필드(`facts`, `evidence`, `llmReview`, `datePrecision`, `dateBasis`, `startPrecision`, `rateBasis`, `reportedUsdPerDay`, `storedReading`의 해시 필드 등)는 선택 필드로 남긴다. 이 값이 있어도 없어도 렌더링이 깨지거나 `undefined`·`NaN`이 보이면 안 된다. `interpretation`의 모든 하위 필드와 `reasonKo`·`quote`·`locator`·`holdReason`은 없을 수 있다.
- 뉴스·공시 탭은 모든 이벤트를 발행일 내림차순 한 목록으로 보여 주며 접힌 별도 구역을 두지 않는다. 모든 항목에 종류 태그를 붙이고, `newsCategory`가 없으면 보고서 → `Fleet Status`, 계약 판단·상세가 있으면 `계약`, 나머지 `기타`로 보인다. 계약 판단이 있는 이벤트(`readingDecision` 또는 예전 `llmReview` + 선박)만 상세 카드로, 나머지는 제목·링크 줄로 보인다.
- 내부 모델명·설정·대조 과정은 화면에 내보내지 않는다.
- 원격 데이터는 `shipCount === 62`, `sourceCount === 4`, fleet 배열 길이 일치일 때만 쓴다. 아니면 `data/data_as_of_26_01_07.json`(내장)을 그린다. 회사·출처 수가 바뀌는 변경은 이 확인도 같이 바꿔야 한다.
- 외부 링크는 `https:` URL만 쓴다(`contractSource`, `sourceHref`).
- 타임라인 초기 가로 스크롤은 브라우저 현재 날짜의 분기(10–12월은 Q4)다. 회사 탭을 바꿀 때만 다시 맞추고, 계약 선택이나 데이터 갱신으로는 움직이지 않는다.

## 구현 패턴

- 계산은 `utils/`의 순수 함수로 두고 컴포넌트는 그 결과를 그린다. 컴포넌트 안에서 요율·기간·상태를 새로 계산하지 않는다.
- 환산 일당 표시는 `backlogRateLabel`(bar 안 `$NNNk`, 연장·옵션 행사면 "연장" 접두)과 `economicsDetails`("금액 ÷ 일수 = 값 / 일 · 공시 dayrate 아님")를 쓴다.
- 해석 정보 표시는 `interpretationDetails`(원문 날짜, 배치 이유, 기간·옵션·금액, 포함·제외), 저장 결과 보완 표시는 `storedReadingDetails`를 쓴다. 없는 항목은 건너뛴다.
- Dayrates 선택값은 URL query `dayrateGen`(기본 `7G`), `dayrateBasis`(`reported`면 공시만, 그 외 환산 포함)에 남긴다.
- 추세선은 `d3-regression`의 `regressionLoess`(2.2.0 고정)를 쓴다. 음영은 가까운 잔차의 표본표준편차이며 신뢰구간으로 표기하지 않는다.

## 테스트

- 실행: `npm run test:ui`(Node 테스트 러너가 TypeScript를 그 자리에서 변환해 `utils/`와 컴포넌트를 불러오고 `react-dom/server`로 렌더링한다), 그리고 `npm run build`(타입 검사 포함).
- `tests/publishedData.test.cjs`만 현재 `public/data`를 읽으며, 형식·무결성(내용 주소 이름, 개수 일치, 날짜·ID·상태 유효, 공시/환산 분리, 판단 형식)만 검사한다. 특정 선박·기사·개수를 전제하는 검사를 여기에 넣지 않는다.
- 특정 사례는 고정 fixture `tests/fixtures/legacy-fleet/`(2026-10-19 게시 스냅샷)이나 테스트 안 입력으로 검사한다. 이 fixture는 예전 필드 호환 검증에도 쓰이므로 새 데이터로 교체하지 않는다.
- 반드시 유지할 검사: bar 스타일(Firm/Option/Contingent)과 선택 상세, 미공개 표시, 환산 일당 표시와 계산식 문구, 대략 날짜 계약의 bar 표시와 통계 포함, 예전 하위 필드가 남은 row·이벤트의 렌더링, 기사만 있고 계약 row가 없으면 bar 없음, Dayrates 세대 선택·7G+ 2025년 시작·공시/환산 선택·표본 부족 시 추세선 미생성, 뉴스 목록의 날짜순 한 목록과 태그 대체 규칙, 현재 분기 초기 스크롤.
- 경계 사례: `interpretation`이 `null`이거나 하위 필드가 모두 없음, `derivedUsdPerDay`가 0·음수·`null`, `dayRate` 0, `includes`가 배열이 아님, `publishedAt`이 없거나 ISO가 아님, 종료일이 타임라인 범위를 넘음, 같은 선박의 경계 달 공유 계약.
