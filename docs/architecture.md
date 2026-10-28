# 아키텍처

## 전체 구성

시스템은 세 실행 주체와 하나의 정적 사이트로 이루어진다. 셋은 서로 직접 통신하지 않고, **GitHub `main` 브랜치의 생성 JSON**과 **프로덕션에 배포된 `manifest.json`**을 통해서만 이어진다.

```
공식 출처(IR 페이지·PDF·SEC·Q4 피드)
      │ HTTPS GET (허용 호스트만)
      ▼
┌─────────────────────────┐   push(public/data, data/provenance)   ┌──────────────┐
│ 서버: GitHub Actions     │ ─────────────────────────────────────▶ │ GitHub main  │
│ 6시간마다, 모델 호출 없음 │                                         │              │
└─────────────────────────┘                                         └──────┬───────┘
                                                                           │ Vercel 배포
┌─────────────────────────┐   GET manifest.json(newsQueue)          ┌──────▼───────┐
│ Mac 실행기(launchd 5분)   │ ◀───────────────────────────────────── │ 프로덕션 사이트 │
│ → 임시 clone에서 로컬 읽기 │ ─ push(public/data, data/provenance,   │ (정적 파일)    │
│ → Codex CLI 호출          │        data/news) ───────────────────▶ │              │
└─────────────────────────┘                                         └──────┬───────┘
                                                                           │ GET
                                                                     브라우저(React 앱)
```

- **서버(GitHub Actions `refresh-fleet-data.yml`)**: 공식 출처를 수집하고 문서 지문을 기록과 비교해, 읽어야 할 새 판을 작업 대기열(`manifest.newsQueue`)과 기록 파일에 올린다. Codex를 호출하지 않으며 계약 데이터를 바꾸지 않는다. 저장소 변수 `FLEET_READER_V2_ENABLED`가 `true`일 때만 돈다.
- **Mac 실행기(`automation.news_worker`, launchd)**: 프로덕션 manifest의 대기열만 본다. 대기 작업이 있을 때만 게시기(`automation.update_news`)를 실행한다. 대기열 내용은 실행 명령으로 쓰지 않는다. 게시기가 출처를 직접 다시 수집한다.
- **게시기(`automation.update_news`)**: `main`의 임시 clone에서 로컬 읽기(`automation.review_news`)를 돌리고, 같은 clone에서 Python 테스트·UI 테스트·빌드를 통과한 뒤 허용 경로만 커밋해 push하고, Vercel이 새 manifest와 payload를 실제로 내보내는지 확인한다. **계약 데이터를 바꾸는 유일한 주체**다.
- **화면(React 앱)**: 프로덕션의 `data/manifest.json`을 읽고, 거기 적힌 내용 주소 파일(`fleet.<hash>.json`, `events.<hash>.json`)을 받아 그린다. manifest 검증(62척, 출처 4개)에 실패하면 번들에 들어 있는 2026-01-07 기준 데이터로 떨어진다.

서버와 Mac 모두 같은 수집 코드를 쓰므로, 서버 대기열은 "Mac이 일할 때가 됐다"는 신호일 뿐이다. Mac은 실행할 때 다시 수집하며, 그 사이 새로 나온 문서도 같은 실행에서 함께 처리한다.

## 모듈 지도

Python 패키지 `automation/fleet_sync`가 핵심이고, `automation/*.py`는 진입점이다. 의존 방향은 위에서 아래로만 흐른다.

| 모듈 | 역할 | 의존 |
|---|---|---|
| `reading_contracts.py` | Codex 응답·입력·대기열의 형식(JSON 스키마), 화면 안전 확인, 환산 일당 계산, 겹침 의심 쌍 찾기 | `model.py`만. 입출력 없음 |
| `reading_service.py` | 요청문 선택(보고서·기사·저장 결과·제목 선별), 형식 오류 재요청(최대 3회), 겹침 시 한 번 더 묻기, 사용 한도 오류 정의 | `reading_contracts` |
| `codex_reader.py` | Codex CLI 어댑터. 로그인 확인, 격리 실행, 페이지 이미지 첨부, 사용 한도·해제 시각 판별 | `reading_service`, `reading_contracts` |
| `news_jobs.py` | 문서 기록과 작업 대기열의 순수 로직(작업 식별, 완료·실패·백오프, 예전 기록 이전, 새 계약 ID) | `core`, `reading_contracts` |
| `sources.py` | 출처 정의(`SOURCE_SPECS`), 허용 호스트 HTTP 클라이언트, 보고서·기사 목록 발견 | `core`, `model` |
| `source_materials.py` | 문서 본문 수집, PDF 페이지 텍스트·이미지와 HTML 본문 추출, 문서 지문 계산 | `sources`, `document_ocr`, `reading_contracts` |
| `document_ocr.py` | 텍스트 없는 PDF 페이지의 Tesseract OCR(보조) | 외부 `tesseract` |
| `reading_pipeline.py` | 한 번의 서버/로컬 실행 전체. 수집→등록→선별→읽기→적용→무결성→기록→공개 파일 쓰기 | 위 전부, `pipeline.validate_fleet` |
| `pipeline.py` | CLI 진입점(`python -m automation.fleet_sync`)과 전체 데이터 무결성 검사, 변경 요약 | 실행 시 `reading_pipeline` |
| `automation/review_news.py` | 로컬 읽기 진입점(현재 checkout에 씀) | `reading_pipeline` |
| `automation/update_news.py` | 임시 clone·검증·push·배포 확인 게시기 | clone 안의 `review_news` |
| `automation/news_worker.py` | launchd 주기 실행. 대기열 확인과 대기 판단 | `update_news`, `reading_contracts` |
| `automation/install_news_worker.py` | launchd 에이전트 설치·제거 | macOS `launchctl` |

외부 효과는 모두 교체 가능한 포트로 들어온다. `execute()`는 수집기(`collector`), 재확인(`recheck`), 읽기 서비스(`service`), 시계(`now`)를 인자로 받고, 읽기 서비스는 `ReaderAdapter`(실제는 `CodexReader`)를 받는다. 테스트는 이 포트에 대역을 넣는다.

화면은 `App.tsx`(데이터 적재·통계 카드·탭), `components/`(Fleet Status 타임라인, Dayrates 산점도, 뉴스·공시 목록, 시장 차트), `utils/`(순수 계산: 기간·요율·추세·선박 유효 상태), `types.ts`(공개 JSON 타입)로 나뉜다. 화면은 공개 JSON만 읽고 Python 코드에 의존하지 않는다.

## 대표 흐름

### 서버 감지 (6시간마다, 매시 17분)

1. `main`의 정확한 revision을 자격 증명 없이 checkout하고, lockfile로 Python 3.12·Node 22 의존성과 `tesseract-ocr`를 설치한다.
2. `pytest` → `python -m automation.fleet_sync --write --mode server` → `npm run test:ui` → `npm run build`.
3. 서버 모드는 4개 회사의 최신 보고서와 기사 목록(회사별 최대 20건)을 수집해 각 문서의 지문을 계산하고, 기록에 없는 (문서, 지문) 쌍을 `pending` 작업으로 등록한다. 예전 파이프라인이 이미 처리한 문서는 처음 볼 때 `completed/migrated`로 옮긴다. 판별은 보고서면 예전 기록의 원본 바이트 해시(HTML 보고서도 지문이 아니라 받은 바이트의 해시로 비교), 기사면 URL이나 예전 방식의 본문 해시다.
4. `public/data`와 `data/provenance`만 artifact로 묶어, 쓰기 권한이 있는 별도 job이 경로 허용 목록과 checksum을 다시 확인한 뒤 커밋·push한다. 그동안 `main`이 움직였으면 게시를 거부하고 다음 주기를 기다린다.
5. Vercel이 새 `dataHash`·`stateFingerprint`·파일 이름을 내보낼 때까지 최대 4분 확인한다.

### Mac 읽기와 게시 (5분마다 확인)

1. `news_worker`가 프로덕션 `manifest.json`을 받아 대기열이 schemaVersion 2이고 fingerprint가 jobs와 맞는지 확인한다. `pendingCount`가 0이면 끝난다. 사용 한도 대기 중이거나, 같은 대기열에 대한 백오프 중이면 기다린다(서버가 다른 작업을 올리면 백오프를 무시한다).
2. `update_news`가 잠금을 잡고 `main`을 임시 디렉터리에 clone한다. clone 안에서 `review_news`를 실행하되, Codex 응답 캐시는 원래 checkout의 `.cache/reading-responses.json`을 쓴다.
3. 로컬 실행: 수집·등록 → 선별 대기 기사들의 제목을 **한 번의** Codex 호출로 선별·태그 → 선택된 기사와 보고서 작업을 하나씩 읽음(보고서 먼저, 기사는 발행일 순, 저장 결과 보완은 마지막) → 각 작업은 직전 작업까지 반영된 fleet 전체를 기준으로 받는다.
4. 각 작업마다: Codex 응답 → 화면 안전 확인 → 원문을 다시 받아 지문 비교(바뀌었으면 적용하지 않고 새 판으로 재등록) → fleet 사본에 적용 → 전체 무결성 검사(실패하면 그 문서 결과 전체를 버리고 작업 실패) → 이벤트·기록 갱신.
5. 태그가 없는 예전 뉴스 이벤트가 있으면 제목만으로 한 번 태그를 붙인다.
6. 공개 파일을 쓰고, 같은 clone에서 테스트·빌드를 통과하면 허용 경로만 커밋해 force 없이 push한다. `main`이 그 사이 움직였으면 새 clone부터 다시 한다(최대 3회, 응답 캐시 덕분에 Codex를 다시 부르지 않는다).
7. 프로덕션 manifest 전체가 로컬 결과와 같고 fleet·events payload 내용과 해시가 맞을 때까지 최대 10분 확인한다. 확인되어야 실행기가 성공으로 기록한다.

### 화면 적재

`data/manifest.json`(캐시 금지) → `shipCount === 62`·`sourceCount === 4`·`fleetFile` 확인 → `fleet.<hash>.json`, `events.<hash>.json`(내용 주소라 브라우저 캐시 허용) → 화면 계산. 어느 단계든 실패하면 번들 데이터로 그리고 콘솔 경고만 남긴다.

## 경계

- **Codex로 넘어가는 것:** 요청문, `INPUT_JSON`(문서 페이지 텍스트, 62척 전체 fleet와 기존 계약·ID, 같은 회사의 보류 항목), PDF 페이지 PNG 이미지, 출력 JSON 스키마(대상 회사 선박 ID로 제한). **넘어가지 않는 것:** 셸·도구·파일 쓰기·웹 검색·배포 권한, 사용자 Codex 설정, 저장소 파일, API 키 환경변수. Codex의 출력은 JSON 하나이며, 적용 여부는 코드가 정한다.
- **서버로 넘어가지 않는 것:** Codex 인증과 모델 호출. 서버는 지문 비교와 대기열 게시까지만 한다.
- **브라우저로 넘어가는 것:** 공개 JSON 네 종류뿐이다. 기록 파일(`data/provenance/readings.json`)과 예전 기록은 저장소에 있지만 화면은 읽지 않는다.

## 외부 의존

- 공식 출처: Transocean(`www.deepwater.com`), Valaris(`www.valaris.com`, `s23.q4cdn.com`의 Valaris 경로, SEC CIK 314808), Noble(`noblecorp.com`, `noble.gcs-web.com`), Seadrill(`www.seadrill.com` WordPress API, `ir.seadrill.com`). 한국 DART는 쓰지 않는다.
- Codex CLI(`codex exec`)와 운영자 ChatGPT 계정.
- GitHub(저장소 `contiloop/drillship_status`, Actions, 저장소 변수), Vercel(`main` 배포, 프로덕션 `https://offshore-drillship-fleet-analyst.vercel.app`).
- macOS launchd, Tesseract(선택적 OCR).
