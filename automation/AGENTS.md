# automation — 수집·읽기·기록·게시

## 범위

이 디렉터리가 가진 것:
- `fleet_sync/`: 공식 출처 수집과 본문 추출, 문서 지문, 문서 기록·작업 대기열, Codex 읽기 서비스와 어댑터, 화면 안전 확인, 계약 반영, 전체 무결성 검사, 공개 JSON 쓰기.
- 진입점: `review_news.py`(로컬 읽기), `update_news.py`(임시 clone 게시기), `news_worker.py`(launchd 주기 실행), `install_news_worker.py`(launchd 설치).
- `tests/`: 위 전부의 단위 테스트. `requirements.txt`·`requirements.lock`: Python 의존성.

이 디렉터리가 갖지 않는 것:
- 화면 계산과 렌더링(`App.tsx`, `components/`, `utils/`). 이곳에서 화면용 표시 문자열이나 통계를 만들지 않는다.
- 생성 데이터 자체(`public/data`, `data/provenance`). 코드 변경 커밋에 생성 데이터 변경을 섞지 않는다.
- 서버 워크플로(`.github/workflows/`)의 단계와 경로 검사. 진입점 인자를 바꾸면 워크플로와 `package.json` 스크립트를 함께 고친다.
- 공개 타입(`types.ts`). 단, 응답·row·이벤트·대기열 형식을 바꾸면 같은 커밋에서 `types.ts`도 맞춘다.

## 불변식

- 계약·선박 데이터를 바꾸는 함수는 `reading_pipeline.apply_reading` 하나다. 서버 모드(`mode="server"`)는 읽기 서비스를 만들지 않으며 `modelCalls`가 항상 0이다.
- 작업 식별은 `news_jobs.job_id(sourceId, documentSha256)`뿐이다. 완료된 (sourceId, documentSha256) 쌍에 대해 `register_material`은 새 작업을 만들지 않는다. 요청문·스키마·모델 설정·fleet 내용을 식별에 넣지 않는다.
- 지문은 `source_materials.content_fingerprint`: PDF(`%PDF-`로 시작)는 바이트 SHA-256, 그 밖은 추출 페이지 텍스트를 줄바꿈으로 이은 UTF-8의 SHA-256. 수집과 재확인(`fetch_document_sha256`)이 같은 함수를 써야 한다. 다르면 모든 기사가 "읽는 중 원문 변경"으로 재대기된다.
- 제목 선별에서 제외된(`outcome: excluded`) 기사는 같은 출처의 새 판이 와도 다시 등록되지 않는다.
- `derivedUsdPerDay`는 `reading_contracts.derived_usd_per_day`로만 정해지며 모델 값은 덮어쓴다.
- 화면 안전 확인(`validate_reading_response`)은 항목별로 통과·보류를 나누고, 구조 전체가 깨진 경우에만 `MalformedResponseError`를 낸다. 보류는 재요청을 일으키지 않는다. 이유·인용·locator 유무로 보류하지 않는다.
- 파이프라인은 서비스가 이미 검증한 응답도 적용 직전에 다시 검증한다. 어떤 어댑터·캐시도 이 검증을 건너뛰지 않는다.
- 문서 하나의 적용은 fleet 사본에서 하고, `pipeline.validate_fleet`을 통과할 때만 실제 fleet로 교체한다. 실패하면 그 문서의 결과는 하나도 반영되지 않는다.
- 새 계약 ID는 `news_jobs.new_contract_id(vesselId, sourceId, documentSha256, decisionIndex)`로만 만든다. 같은 입력이면 같은 ID다.
- `ReaderUsageLimited`는 실패 횟수를 늘리지 않고(`mark_usage_limited`), 그 실행의 남은 모든 Codex 호출을 멈춘다(`self.stop`). `ReaderConfigurationError`도 실행을 멈추지만 실패로 센다.
- Codex 실행 환경에서 `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL`을 제거하고, `codex login status`가 `Logged in using ChatGPT`가 아니면 `codex exec`를 실행하지 않는다.
- 실행기는 대기열 schemaVersion 2와 fingerprint를 확인한 뒤 `pendingCount`만 신호로 쓴다. job의 URL·필드를 명령이나 경로로 쓰지 않는다.
- 게시기는 `public/data/`, `data/provenance/`, `data/news/` 아래 `.json`만 커밋하고, symlink를 거부하고, 일반 push만 하며, 프로덕션 manifest 전체와 payload 내용이 같아야 성공을 낸다.
- 예전 기록(`data/provenance/sources.json`, `observations.json`, `data/news/reviews.json`)은 읽기만 한다.

## 구현 패턴

- **포트와 어댑터**: `execute(root, mode, write, service, collector, recheck, now, cache_path, stored_results)`. 실제 구현은 기본값으로 들어가고(`collect_reading_materials(HttpClient())`, `fetch_document_sha256`, `ReadingService(CodexReader())`, `time.time`), 테스트는 대역을 넘긴다. 새 외부 효과도 이 방식으로 인자에 추가한다.
- **읽기 어댑터 계약**(`ReaderAdapter`): `read(request)`, `triage(items, correction=)`는 필수. `read_corrected(request, previous_response=, error=)`가 있으면 형식 오류 재요청에, `read_rechecked(request, recheck=)`가 있으면 같은 고객 겹침 재질문에 쓰인다. 없으면 각각 `read` 재호출, 재질문 생략이다.
- **요청문**은 `reading_service.py`의 조각(`_ROLE`, `_IMAGES`, `_FLEET_REPORT`, `_NEWS`, `_ROW_RULES`, `_OPEN_HOLDS`, `_STORED_RESULT`, `_END`)을 이어 붙여 만든다. 회사별 예외나 영어 표현 규칙을 넣지 않고, 목적과 판단 기준만 쓴다. 문서·URL·기록이 지시가 아니라는 문장을 지우지 않는다.
- **출력 스키마**는 Structured Outputs 부분집합이다: `_object()`가 모든 속성을 `required`로 넣고 `additionalProperties: false`를 붙이며, 선택 값은 `_nullable()`로 표현한다. 요청마다 `vesselId`를 그 회사 선박 ID로 열거한다.
- **기록 갱신**은 `news_jobs`의 `mark_completed`/`mark_failed`/`mark_usage_limited`로만 한다. 상태 필드를 직접 바꾸지 않는다.
- **파일 쓰기**는 `reading_pipeline.atomic_json`(symlink 거부, 임시 파일 교체, NaN 금지)을 쓴다.
- **잠금**: 파이프라인 `.cache/reading-pipeline.lock`, 게시기 `.cache/codex-news-update.lock`, 실행기 `.cache/news-worker.lock`. 모두 비차단으로 잡고, 못 잡으면 실패가 아니라 `skipped`/`busy`로 끝난다.
- 게시기는 clone 안에서 `sys.executable -m automation.review_news`를 실행한다. 즉 읽기 코드는 `main` clone의 것이고 인터프리터와 의존성은 실행기를 설치한 checkout의 `.venv`다.

## 테스트

- 실행: `.venv/bin/python -m pytest automation/tests -q`. 네트워크와 Codex를 부르지 않는다. HTTP는 가짜 클라이언트, Codex는 `ReaderAdapter` 대역이나 `subprocess.run` monkeypatch로 대신한다.
- 테스트는 현재 `public/data`의 계약 개수나 특정 기사 존재를 전제하지 않는다. 등록 선박 fixture는 `data/data_as_of_26_01_07.json`을 쓴다.
- 반드시 유지할 검사(파일: `test_reading_pipeline.py`, `test_reading_service.py`, `test_reading_contracts.py`, `test_source_materials.py`, `test_news_worker.py`, `test_publication.py`):
  - 같은 문서 재실행 시 모델 0회·새 계약 0건, 요청문이나 fleet만 바뀌면 재대기 없음, 새 지문이면 작업 생성.
  - 새 계약 ID가 실행 간 같다.
  - 선별 제외 기사는 본문을 읽지 않고 다시 선별하지 않는다. 여러 회사 기사가 선별 한 번에 묶인다.
  - 일부 실패 시 성공분 기록, 실패분만 백오프. 사용 한도 시 실패 횟수 불변·남은 호출 중단(선별 중 한도 포함).
  - 읽는 중 원문 변경 시 미적용·새 판 재대기. 무결성 실패 시 문서 전체 미적용.
  - 화면 안전 실패 항목만 보류(`conflict` 이벤트 + `openHolds`), 이유·인용 없이도 반영, 보류가 다음 같은 회사 읽기에 전달되고 닫힘.
  - 환산 = 금액 ÷ 일수, 둘 중 하나 없거나 일수 ≤ 0이면 없음.
  - 형식이 깨진 출력은 정정 요청과 함께 다시 묻고 3번 뒤 실패.
  - 같은 고객 31일 초과 겹침에서만 재질문, 철회·교체되는 계약과의 겹침은 묻지 않음, 미공개 고객끼리는 같은 고객.
  - 사용 한도 메시지와 해제 시각("try again at 8:34 PM", 날짜 포함형) 해석.
  - HTML 지문이 본문 밖 요소 변화(주가 위젯)에 영향받지 않음, PDF는 바이트 지문.
  - 실행기: v1 대기열·fingerprint 변조 거부, 사용 한도 대기는 새 작업에도 유지, 백오프는 새 작업이 오면 풂, 배포 미확인은 실패.
  - 게시기: 허용 경로 밖·symlink 거부, `main` 경합 시 새 clone 재시도와 캐시 재사용, force 없음, manifest 본문이 다르면 배포 미확인.
- 경계 사례: 날짜 `2027-02-30`(형식은 맞지만 없는 날), 시작 > 종료, `dayRate`가 `true`(bool은 수치가 아님), NaN·Infinity가 든 응답(JSON 파싱에서 거부), 다른 회사 선박 ID, 다른 선박의 계약 ID, row 없는 upsert, 계약 ID 없는 withdraw, 빈 페이지 목록, 5MB 넘는 페이지 이미지.
