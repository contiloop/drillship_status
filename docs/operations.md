# 운영

## 사전 조건

- macOS(실행기는 launchd 전용), Homebrew
- 저장소 `contiloop/drillship_status`에 push할 수 있는 git 자격 증명, 저장소 변수·워크플로를 다룰 `gh` CLI(로그인 상태)
- Codex CLI와 운영자 ChatGPT 계정
- 네트워크(공식 출처, GitHub, Vercel, npm·PyPI)

## 초기 설정 (새 Mac 또는 재설치)

저장소 루트에서 순서대로 실행한다. 2단계는 1단계의 Python 3.12가, 4단계는 1단계의 Node 22가 있어야 한다.

```bash
# 1. 런타임과 OCR
brew install python@3.12 node@22 tesseract

# 2. Python 가상환경 (경로는 반드시 저장소의 .venv: npm 스크립트와 실행기가 이 경로를 쓴다)
python3.12 -m venv .venv

# 3. 해시 고정 의존성
.venv/bin/python -m pip install --require-hashes -r automation/requirements.lock

# 4. Node 의존성
npm ci

# 5. Codex 로그인 (ChatGPT 계정. API 키를 쓰지 않는다)
codex login
codex login status        # "Logged in using ChatGPT"가 나와야 한다

# 6. 확인
npm run verify
```

`codex`, `node`, `npm`, `git`이 PATH에 있어야 실행기 설치가 통과한다. 설치 때의 PATH가 launchd 설정에 고정되므로, 이 도구들의 설치 위치가 바뀌면 실행기를 다시 설치한다.

## 개발·검증 명령

| 명령 | 하는 일 |
|---|---|
| `npm run dev` | 개발 서버(포트 3000). `public/data`의 현재 생성 데이터를 그린다 |
| `.venv/bin/python -m pytest automation/tests -q` | Python 테스트(네트워크·Codex 없음) |
| `npm run test:ui` | 화면 유틸·컴포넌트 테스트와 생성 데이터 형식 테스트 |
| `npm run build` | 타입 검사 + 프로덕션 빌드 |
| `npm run verify` | 위 셋을 `.venv` Python으로 한 번에 |
| `npm run data:check` | 서버 모드 수집만, 파일 쓰기 없음(네트워크 사용). 결과 JSON의 `pendingJobIds`로 새로 읽힐 문서를 미리 본다 |

## 실행 명령

모두 저장소 루트에서 실행하며 결과를 JSON으로 출력한다.

| 명령 | 하는 일 | 쓰는 곳 |
|---|---|---|
| `.venv/bin/python -m automation.news_worker` | 프로덕션 대기열 확인, 작업이 있으면 게시기 실행 | launchd가 5분마다 |
| `.venv/bin/python -m automation.update_news` | 대기열과 무관하게 `main` 임시 clone에서 로컬 읽기 → 검증 → push → 배포 확인 | 수동 게시 |
| `.venv/bin/python -m automation.update_news --stored-results` | 위 + `data/news/reviews.json` 저장 결과 보완을 한 번 큐에 넣음(완료 기록이 있으면 다시 하지 않음) | 1회성 복구 |
| `.venv/bin/python -m automation.review_news` | 현재 checkout에 로컬 읽기 결과를 씀(push 없음) | 로컬 확인용. 생성 데이터가 바뀐다 |
| `.venv/bin/python -m automation.fleet_sync --write --mode server` | 서버 모드(수집·대기열 게시). Actions와 같다 | 워크플로 |
| `.venv/bin/python -m automation.fleet_sync --write --mode local` | 현재 checkout에서 로컬 읽기(push 없음) | 로컬 확인용 |

## 실행기 설치·제거

```bash
.venv/bin/python -m automation.install_news_worker              # 설치(이미 있으면 교체)
.venv/bin/python -m automation.install_news_worker --uninstall  # 중지·제거
launchctl print gui/$(id -u)/com.contiloop.drillship-news-worker  # 상태
```

설치한 checkout의 `.venv` Python과 경로가 launchd 설정에 들어간다. 실행기·게시기 코드는 이 checkout에서, 읽기 코드는 매번 새로 clone한 `main`에서 돈다. 그래서 이 checkout은 `main`에 두고 최신으로 유지하며, `main`의 `requirements.lock`이 바뀌면 3단계를 다시 한다.

## 환경 설정

| 이름 | 종류·범위 | 뜻 |
|---|---|---|
| `FLEET_READER_V2_ENABLED` | GitHub 저장소 Actions 변수 | `true`일 때만 서버 감지 워크플로(6시간마다, 수동 실행 포함)가 돈다. Mac 실행기가 설치·동작 중일 때만 켠다. 설정: `gh variable set FLEET_READER_V2_ENABLED --body true --repo contiloop/drillship_status` |
| `SEC_USER_AGENT` | 프로세스 환경변수(선택) | SEC 등 출처 요청의 User-Agent. 기본값은 공개 noreply 주소. 워크플로가 같은 값을 넣는다 |
| `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL` | 설정하지 않는다 | 있어도 Codex 실행 전에 제거된다 |
| launchd `PATH` | 설치 시 고정 | `codex`·`node`·`npm`·`git`의 디렉터리와 기본 경로 |

## 배포

- 사이트는 `main`의 정적 빌드를 Vercel이 배포한다. 데이터 갱신도 `main`에 커밋되어 같은 경로로 배포된다.
- 데이터 게시는 두 경로뿐이다. 서버 워크플로(`public/data`, `data/provenance`)와 Mac 게시기(여기에 `data/news` 추가). 둘 다 Vercel이 새 manifest와 payload를 내보내는지 확인해야 성공이다(서버 최대 4분, Mac 최대 10분).
- 서버 워크플로 수동 실행: `gh workflow run refresh-fleet-data.yml --ref main --repo contiloop/drillship_status`. `main`이 아닌 ref에서는 거부된다.

## 운영 전환 (아직 하지 않음, 사용자 승인 뒤 한 번에)

전환 전 프로덕션 `main` 기준 revision은 `4d82ab6`이다. 아래 순서를 바꾸지 않는다. 옛 화면이 새 데이터를 받거나, 새 대기열을 읽을 실행기 없이 서버가 v2 대기열을 게시하는 상황을 막기 위한 순서다.

1. **옛 실행기 중지**: `.venv/bin/python -m automation.install_news_worker --uninstall`. 확인: `launchctl print gui/$(id -u)/com.contiloop.drillship-news-worker`가 실패해야 한다.
2. **`main` 병합과 배포 확인**: 브랜치 `codex/llm-fleet-updates`를 `main`에 병합해 push한다(force 금지). 그 전에 `main`에 옛 서버 워크플로가 만든 데이터 커밋이 새로 생겼는지 `git log origin/main -- public/data`로 확인한다. 생겼다면 `public/data`·`data/provenance` 충돌을 해결해야 한다. Vercel 배포 후 프로덕션 화면이 새 코드(뉴스·공시 탭의 종류 태그)인지 확인한다.
3. **새 실행기 설치**: 설치 checkout을 `main`으로 바꾸고 pull한 뒤 `.venv/bin/python -m automation.install_news_worker`. 확인: `.cache/news-worker-state.json`의 `status`가 `idle` 또는 `processed`.
4. **서버 감지 켜기**: `FLEET_READER_V2_ENABLED=true` 설정 후 워크플로를 한 번 수동 실행한다. 확인: 실행 성공, 프로덕션 manifest의 `newsQueue.schemaVersion`이 2.
5. **복구 데이터 확인**: 복구 결과(`public/data`, `data/provenance/readings.json`)는 이미 브랜치에 커밋되어 있다(`c2a2ad6`). 이 커밋째로 병합하면 2단계에서 함께 게시되고, 여기서 `.venv/bin/python -m automation.update_news --stored-results`를 돌려도 저장 결과 작업이 이미 완료라 일반 실행만 한다. 즉 이 단계는 확인용이다. 확인: 결과 `ok: true`, `deploymentVerified: true`. 데이터 커밋을 빼고 병합했다면 같은 명령이 복구를 새로 실행해 게시한다(Codex 호출이 다시 든다). 어느 쪽으로 병합할지는 전환 때 정한다.
6. **프로덕션 확인**: 무변경 상태에서 실행기를 다시 돌려 `modelCalls: 0`, 화면의 Fleet Status bar·상세·Dayrates·뉴스·공시 탭 정상. 확인하지 못한 항목은 미완료로 남긴다.

되돌리기: 변수를 끄고(`gh variable set FLEET_READER_V2_ENABLED --body false ...`), 새 실행기를 제거하고, 병합 커밋을 `git revert -m 1 <병합 커밋>`으로 되돌려 push한다(force 금지). 되돌린 뒤 `main`의 옛 서버 워크플로는 변수와 무관하게 다시 돈다.

## 진단

| 보고 싶은 것 | 위치 |
|---|---|
| 문서별 상태·결과·오류·다음 시도·보류·변경 전 값 | `data/provenance/readings.json` |
| 마지막 수집·읽기·적용 시각, 사용 한도 대기, 수집·읽기 오류, 회사별 보고서 상태 | 프로덕션 `data/manifest.json`의 `readingStatus`, `sources`, `sourceHealth` |
| 실행기 마지막 확인·대기·오류 | `.cache/news-worker-state.json`, `.cache/news-worker.log`, `.cache/news-worker-error.log` |
| 재시도용 Codex 응답 | `.cache/reading-responses.json` (Git에 넣지 않는다) |
| 직전 게시 대비 회사별 변경 개수 | `public/data/changes.json` |

`.cache/` 전체는 로컬 상태이며 지워도 된다. 지우면 대기 중 작업의 캐시된 응답이 사라져 다음 실행에서 Codex를 다시 부른다.
