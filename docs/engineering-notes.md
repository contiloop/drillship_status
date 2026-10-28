# 엔지니어링 노트

## 문서 지문과 재읽기

### HTML 기사가 매 실행마다 다시 읽힌다
- **증상**: 바뀌지 않은 Transocean 기사가 수집할 때마다 새 판으로 등록되어 Codex 호출이 반복된다.
- **원인**: Transocean 기사 페이지에 실시간 주가 위젯이 있어 원본 HTML 바이트가 요청마다 달라진다. 예전에는 원본 바이트로 지문을 만들었다.
- **대응**: HTML은 추출한 본문 텍스트로, PDF는 바이트로 지문을 만든다(`content_fingerprint`). 지문 계산을 원본 HTML 바이트로 되돌리지 않는다. 비슷한 증상이 다른 사이트에서 보이면, 같은 기사를 두 번 받아 `extract_document_pages` 결과가 다른지부터 확인한다. 다르면 추출 선택자가 본문 밖 요소까지 잡고 있는 것이다.

### 추출 코드를 바꾸면 이미 읽은 기사가 전부 다시 읽힌다
- **증상**: `source_materials.py`의 HTML 추출(선택자, 공백 처리, 블록 태그 목록)을 고친 뒤 첫 수집에서 기사 수십 건이 `pending`이 되고 Codex 호출과 중복 판단이 쏟아진다.
- **원인**: 지문이 추출 텍스트이므로 추출이 바뀌면 모든 HTML 문서의 판이 바뀐다. 제목 선별에서 제외된 기사는 다시 선별하지 않지만, 읽어서 반영했거나 예전 파이프라인에서 이전된 기사는 새 판으로 다시 읽힌다. PDF 보고서는 바이트 지문이라 영향이 없다.
- **대응**: 추출을 바꾸기 전에, 추출 결과가 기존 문서에서 글자 하나라도 달라지는지 확인한다. 바꾼 뒤에는 `npm run data:check`(서버 모드, 파일 쓰기 없음, 네트워크 사용)를 돌려 출력의 `pendingJobIds`가 늘지 않았는지 본다. 늘었다면 병합하지 않거나, 완료 기록의 `documentSha256`을 새 지문으로 옮기는 이전 단계를 함께 만든다.

### 요청문을 고쳐도 기존 데이터가 바뀌지 않는다
- **증상**: `reading_service.py`의 요청문을 개선했는데 화면의 기존 계약이 그대로다.
- **원인**: 완료된 (문서, 지문)은 다시 읽지 않는다. 요청문·스키마·기존 계약은 재읽기 조건이 아니다. 의도된 동작이다.
- **대응**: 개선은 다음 새 판(다음 분기 보고서, 새 기사)부터 적용된다. 특정 문서를 꼭 다시 읽어야 하면 로컬 checkout에서 `data/provenance/readings.json`의 그 문서 기록(같은 `sourceId`의 기록 전부)을 지우고 게시기를 돌린다. 이때 두 가지에 주의한다.
  - 그 URL이 `data/news/reviews.json`에 있거나, `sourceId` 없는 예전 이벤트에 있으면, 처음 보는 문서로 취급되어 다시 읽지 않고 `migrated`로 완료된다.
  - `.cache/reading-responses.json`에 그 작업의 응답이 남아 있으면 Codex를 부르지 않고 그 응답을 다시 쓴다. 새 답이 필요하면 그 키도 지운다.
  - 확인: 실행 결과의 `modelCalls`가 1 이상이고 그 문서 기록이 `completed`로 바뀌었는지 본다.

## Codex 결과의 변동

- **증상**: 같은 문서를 두 번 읽으면 결과가 다르다. 예: 한 번은 Noble Voyager를 Sep-26~Sep-30으로 수정했고, 다른 번은 변경 없음으로 두었다. 현재 데이터의 Voyager에는 환산 일당이 없다.
- **원인**: 모델 출력은 결정적이지 않다. 검증은 화면 안전만 보므로 "덜 읽은" 결과도 반영된다.
- **대응**: 결과를 맞추려고 코드에 의미 검사나 회사별 규칙을 넣지 않는다(사용자 결정). 다음 공시가 배치를 고친다. 정말 틀린 반영이 확인되면 위 강제 재읽기 절차를 쓰고, 다시 읽어도 결과가 달라질 수 있음을 감안한다.

## 같은 계약이 bar 두 개로 생긴다

- **증상**: 이미 있는 수주에 새 bar가 하나 더 생긴다(실제 사례: Noble Venturer–Planet One, VALARIS DS-12, Deepwater Conqueror/Proteus). 연장한 계약 기간이 뒤따르는 옵션 구간과 겹친다.
- **원인**: Codex가 기존 계약 ID를 쓰지 않고 새 계약(`contractId: null`)으로 답하거나, 기간을 늘리면서 다음 구간을 옮기지 않는다.
- **대응**: 서비스가 같은 선박·같은 고객 계약과 31일 넘게 겹치는 row를 찾아 Codex에게 한 번 더 묻는다(`suspected_duplicates`, `DUPLICATE_CHECK_PROMPT`). 막지 않고 두 번째 답을 쓴다. 31일 여유는 인접 구간이 경계 달을 공유하는 배치 관행 때문이니 줄이지 않는다. 같은 고객 판정은 고객명 첫 단어 비교라서 "Shell"과 "Shell Brasil"은 같고 "Petrobras"와 "Petróleo Brasileiro"는 다르다. 다시 물어도 남은 중복은 다음 보고서 읽기에서 Codex가 정리하도록 요청문이 요구한다(하나 수정, 나머지 철회).

## 실행기와 게시기

### 실행기 코드와 읽기 코드는 서로 다른 checkout에서 돈다
- **증상**: 브랜치에서 고친 읽기 동작이 Mac 실행에 나타나지 않거나, 반대로 실행기 쪽만 바뀐다.
- **원인**: launchd는 설치한 checkout(`--root`)의 `news_worker`·`update_news`를 실행한다. 그러나 실제 수집·읽기(`review_news`, `fleet_sync`)는 게시기가 만든 **`main`의 임시 clone**에서 돈다. Python 인터프리터는 설치 checkout의 `.venv`다.
- **대응**: 실행기를 설치한 checkout은 `main`에 두고 `main`이 바뀌면 pull한다. `main`에서 `automation/requirements.lock`이 바뀌면 설치 checkout의 `.venv`에 다시 설치한다. 그러지 않으면 clone 안의 pytest가 import 오류로 실패해 게시가 멈춘다. 확인: `.cache/news-worker-state.json`의 `lastError`.

### 전환 전 실행기가 "news queue must be schemaVersion 2"로 실패한다
- **증상**: 설치된 실행기(`com.contiloop.drillship-news-worker`, 이 checkout의 코드)가 5분마다 이 오류로 실패하고 백오프한다.
- **원인**: 프로덕션(`main`)이 아직 schemaVersion 1 대기열을 게시한다. 새 실행기는 v1을 읽지 않는다.
- **대응**: 해롭지 않다. 모델을 부르지 않고 아무것도 게시하지 않는다. 운영 전환으로 `main`이 v2를 게시하면 사라진다. 전환 후에도 계속되면 프로덕션 manifest의 `newsQueue.schemaVersion`을 확인한다.

### 응답 캐시
- `.cache/reading-responses.json`은 임시 clone이 버려져도 남아, `main` 경합으로 처음부터 다시 할 때 Codex를 다시 부르지 않게 한다. 기록에서 완료된 작업과 기록에 없는 작업의 항목은 다음 실행에서 지워진다. 전체 무결성 검사에 실패한 문서의 응답도 지워져 다음 시도에서 새로 묻는다.
- 캐시된 응답이 현재 기준으로 형식이 깨졌으면 무시하고 새로 묻는다. 그러나 형식만 맞으면 오래된 응답도 그대로 쓴다. 대기 중인 작업을 새로 읽히고 싶으면 그 `jobId` 키를 지운다.

### 개발 checkout에서 로컬 읽기를 돌리면 생성 데이터가 바뀐다
- `python -m automation.review_news`, `python -m automation.fleet_sync --write --mode local`은 현재 checkout의 `public/data`와 `data/provenance`를 덮어쓴다. 프로덕션 게시는 항상 `python -m automation.update_news`(임시 clone)로 한다. 실수로 돌렸다면 `git checkout -- public/data data/provenance`로 되돌리고, 새로 생긴 `public/data/*.json`은 `git clean -n public/data`로 확인 후 지운다.

### `main`이 움직였을 때
- Mac 게시기는 push 전에 원격 `main`을 다시 보고, 바뀌었으면 새 clone부터 다시 한다(최대 3번). 서버는 그 실행을 포기한다. 서버와 Mac이 동시에 돌아 서로 밀어내는 일은 드물고, 다음 주기에 해소된다. 생성 데이터를 손으로 고쳐 커밋하면 파일 이름·manifest 해시와 내용이 어긋나 두 게시기 모두 "content hash does not match manifest"로 실행마다 실패한다.

## 사용 한도

- **증상**: 실행 결과에 `usageLimitedUntil`이 찍히고 남은 작업이 모두 대기한다.
- **원인**: Codex CLI가 "You've hit your usage limit ... try again at 8:34 PM" 같은 오류를 낸다. 코드는 해제 시각을 Mac 현지 시각으로 해석하고(날짜가 없으면 다음에 오는 그 시각), 시각이 없으면 1시간 뒤로 잡는다.
- **대응**: 기다리는 것이 정답이다. 실패 횟수는 늘지 않고 다른 모델로 넘어가지 않는다. 실행기는 해제 전에는 서버가 새 작업을 올려도 기다린다. 확인: 공개 manifest의 `readingStatus.usageLimitedUntil`, `.cache/news-worker-state.json`의 `usageLimitedUntil`.

## 수집

- **Noble gcs-web 403**: 일부 `noble.gcs-web.com` 기사 URL이 자동 요청에 HTTP 403을 준다. 수집 오류로 기록되고 다음 수집마다 다시 시도한다. 우회하지 않는다. 받지 못한 기사는 기록이 없으므로 대기열·뉴스 목록에도 없다.
- **PDF 한도**: 페이지를 144 DPI PNG로 그려 Codex에 보낸다. 한 페이지가 1천만 픽셀이나 5MB를 넘거나 문서 전체 이미지가 40MB를 넘으면 그 문서 수집 전체가 실패한다(페이지를 빼고 보내지 않는다). 기존 데이터는 그대로다.
- **OCR**: 텍스트 층이 없는 PDF 페이지만 Tesseract로 읽는다. Tesseract가 없거나 실패해도 경고만 남고 그 페이지는 이미지로 읽힌다. 실패 사유는 문서의 `extractionWarnings`에 있다.
- **보고서 발견 실패**: 회사 IR 페이지에서 보고서 링크를 못 찾으면 `SOURCE_SPECS`의 `fallback_document_url`(고정 URL)을 쓴다. 이 URL은 시간이 지나면 낡으므로, 발견 오류가 계속되면 발견 로직을 고치고 대체 URL도 최신으로 바꾼다. Valaris는 공식 페이지 실패 시 SEC submissions에서 fleet status exhibit를 찾는다.
- **기사 목록 한도**: 회사별 목록에서 최대 20건만 본다. 대기 중인 작업의 문서는 목록에서 밀려나도 계속 다시 받는다.

## Codex 호출

- 호출당 제한 시간은 600초다. 넘으면 실행 오류로 그 작업만 백오프한다.
- 출력 스키마가 Structured Outputs 규칙을 어기면 Codex가 `invalid_json_schema`를 내고, 코드는 이를 공통 설정 오류로 보고 그 실행의 남은 읽기를 모두 멈춘다. 스키마를 바꿀 때 선택 필드도 `required`에 넣고 타입을 `[T, "null"]`로 둔다.
- 페이지 이미지는 첨부 순서와 페이지 locator를 잇는 목록(`PAGE_IMAGE_ATTACHMENTS_JSON`)과 함께 보낸다. 입력 JSON에는 base64 대신 이미지의 sha256과 순번만 들어간다.
- 새 계약 ID는 최종 응답의 판단 순서로 정해진다. 겹침 재질문으로 두 번째 답의 순서가 바뀌면 첫 답과 다른 ID가 나오지만, 응답이 캐시되고 문서가 완료되면 이후로는 바뀌지 않는다.

## 화면

- manifest의 `shipCount`가 62가 아니거나 `sourceCount`가 4가 아니면 화면은 내장 데이터(2026-01-07)로 그리고, 상단에 작은 글씨로 "기본 데이터"와 "Live sync fallback: …"만 보인다. 화면이 멀쩡해 보여도 반년 전 데이터일 수 있다. 출처 회사가 늘거나 줄면 이 확인도 같이 바꿔야 한다. 확인: 상단에 "공식 출처 4/4"가 보이는지.
- 기사·이벤트의 `readingDecision.row`는 표시용이다. 반영되지 않은 row(보류, 화면 안전 실패)로 bar를 만들면 안 된다.

## 운영 전환·변경 후 확인 순서

1. 네 가지 검증 명령 통과(`pytest`, `npm run test:ui`, `npm run build`, `npm run verify`).
2. `npm run data:check`: `ok: true`, 의도하지 않은 `pendingJobIds` 증가 없음.
3. 게시 후: 프로덕션 `data/manifest.json`의 `stateFingerprint`가 게시 결과와 같고, `newsQueue.schemaVersion`이 2, `pendingCount`가 기대값인지.
4. 같은 상태에서 실행기를 한 번 더 돌려 `modelCalls`가 0인지(무변경 수집에서 모델을 부르지 않는지).
5. 화면: Fleet Status bar·계약 상세, Dayrates(세대 전환·환산 포함 토글), 뉴스·공시 탭(날짜순 한 목록, 종류 태그).
