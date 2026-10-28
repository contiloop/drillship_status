# 보안

## 보호 대상

| 대상 | 위치 | 피해 시 결과 |
|---|---|---|
| Codex ChatGPT 로그인 세션 | 운영자 Mac의 Codex CLI 저장소(사용자 홈) | 운영자 계정 사용량 도용, 대체 경로로 다른 모델이 쓰일 위험 |
| `main` 쓰기 권한 | Mac: 운영자 git 자격 증명. 서버: 워크플로 publish job의 `GITHUB_TOKEN` | 공개 데이터·코드 변조, 이력 손상 |
| 공개 계약 데이터의 무결성 | `public/data`, `data/provenance` | 공개 화면에 잘못된 계약·선박 노출 |
| 읽기 경로의 격리 | Codex 실행 환경 | 문서 속 지시문에 의한 파일 변경·명령 실행 |

공개 데이터 자체는 비밀이 아니다. 화면 이용자에게 로그인·편집 기능은 없다.

## 인증 흐름

**Codex 읽기 (Mac)**

1. `codex` 실행 파일을 PATH에서 찾는다. 없으면 실행 오류로 그 작업이 실패하고 백오프한다.
2. `OPENAI_API_KEY`, `CODEX_API_KEY`, `OPENAI_BASE_URL`을 뺀 환경으로 `codex login status`를 실행한다. 출력에 정확히 `Logged in using ChatGPT` 줄이 있어야 한다. API 키 로그인, 로그아웃, 세션 만료는 모두 여기서 걸려 실행 오류가 된다(작업 실패, 백오프). 운영자가 `codex login`을 다시 해야 풀린다.
3. 사용 한도 응답은 인증 실패가 아니다. 해제 시각까지 기다린다.
4. 어떤 실패에서도 API 키나 다른 모델로 넘어가지 않는다.

**GitHub 게시**

- 서버: 수집·테스트를 하는 validate job은 `contents: read`이고 checkout 자격 증명을 남기지 않는다. 쓰기는 publish job만 `contents: write`로 하며, artifact의 base revision·경로·checksum과 원격 `main`이 일치할 때만 push한다.
- Mac: 운영자의 git 자격 증명으로 `origin`에 push한다. 커밋 작성자는 `contiloop` noreply 주소로 고정한다.

## 권한

| 주체 | 할 수 있는 것 | 할 수 없는 것 |
|---|---|---|
| 화면 이용자 | 공개 JSON과 화면 조회 | 쓰기 전부 |
| 서버(GitHub Actions) | 공식 출처 수집, `public/data`·`data/provenance` 게시 | Codex 호출, 계약·선박 데이터 변경, `data/news`·코드 경로 게시, `main`이 움직인 뒤의 게시 |
| Mac 게시기 | 수집, Codex 호출, `public/data`·`data/provenance`·`data/news`의 JSON 게시 | 그 밖의 경로 커밋, symlink 경로 쓰기, force push, 테스트·빌드 실패 상태의 게시 |
| Codex(모델) | 주어진 입력을 읽고 스키마에 맞는 JSON 하나를 반환 | 셸·도구·앱·플러그인·메모리·웹 검색, 파일 쓰기(sandbox read-only), 사용자 Codex 설정·규칙·프로젝트 문서 읽기, 대상 회사 밖 선박 ID 사용 |
| 운영자 | 실행기 설치·제거, 수동 게시, 저장소 변수 설정, 전환 승인 | — |

## 모델 격리

- `codex exec`는 매번 새 임시 디렉터리에서 `--ignore-user-config --ignore-rules --ephemeral --skip-git-repo-check --sandbox read-only`로 실행한다. 설정으로 셸 도구·앱·플러그인·메모리·multi-agent·JS REPL·번들 스킬·웹 검색을 끄고, 승인 정책은 `never`, 프로젝트 문서 읽기 한도는 0이다.
- 문서 본문, 페이지 이미지, URL, 저장된 기록, 이전 출력은 **자료이지 지시가 아니다.** 요청문이 이를 명시하고, 그 안의 지시를 무시하라고 한다. 대기열의 URL·작업 내용도 실행 명령으로 쓰지 않는다. 실행기는 대기열을 신호로만 쓰고 출처를 직접 다시 수집한다.
- 출력은 JSON 스키마로 제한되고(대상 회사 선박 ID만 허용), 코드는 화면 안전 확인을 통과한 항목만 적용한다.
- 페이지 이미지 파일 이름은 첨부 순번으로만 만든다(문서가 준 locator를 파일 이름에 쓰지 않는다).

## 출처 접근

- HTTPS만, 허용 호스트만 요청한다. 리다이렉트는 최대 5번이며 매 단계 다음 주소를 요청 전에 검사한다. 응답 크기는 GET 25MB, POST 5MB로 제한한다.
- 회사별로 발행사 소유 호스트만 공식 출처로 인정한다. 공유 저장소는 경로·식별자까지 확인한다(`s23.q4cdn.com`은 Valaris 경로만, SEC는 Valaris CIK 314808만). URL에 사용자 정보나 443 외 포트가 있으면 거부한다.
- 봇 차단(HTTP 403 등)을 우회하지 않는다. 오류로 기록하고 다음 수집에 다시 시도한다.
- SEC 요청의 User-Agent는 `SEC_USER_AGENT`(기본값: 공개 noreply 주소)다. 개인 이메일을 넣지 않는다.

## 게시 경로

- 자동 커밋은 `public/data/`, `data/provenance/`, `data/news/` 아래의 `.json` 파일만 허용한다. 절대 경로·`..`·symlink·디렉터리는 거부한다. 서버는 그중 `public/data`와 `data/provenance`만 쓴다.
- 기록·공개 파일을 쓸 때 대상 경로나 상위 경로가 symlink이면 거부한다.
- push는 항상 일반 push다. `main`이 움직였으면 Mac은 새 clone부터 다시 하고(최대 3번), 서버는 그 실행을 포기한다.

## 기록 대상

| 사건 | 기록 위치 |
|---|---|
| 문서별 상태·결과·마지막 읽은 시각·실패 오류·실패 횟수·다음 시도 시각 | `data/provenance/readings.json`의 문서 기록 |
| 계약 추가·수정·철회와 선박 상태 변경의 변경 전 값, Codex의 이유·인용(있을 때) | 같은 기록의 `changes` |
| 보류 항목과 보류 이유 | 같은 기록의 `openHolds`, 공개 이벤트(`reviewStatus: conflict`) |
| 수집 오류(회사·URL·HTTP 상태·마지막 성공 시각) | 기록의 `collectionErrors`, 공개 manifest의 `readingStatus` |
| 사용 한도 대기 시각 | 공개 manifest의 `readingStatus.usageLimitedUntil`, 실행기 상태 파일 |
| 실행기 확인 결과·오류 | Mac `.cache/news-worker-state.json`, `.cache/news-worker*.log`(저장소에 넣지 않음) |

**남기지 않는 것**: 모델 응답 원문 전체(Mac의 `.cache/reading-responses.json`에 재시도용으로만 있다. 완료된 작업과 기록에 없는 작업의 응답은 다음 로컬 실행을 시작할 때 지워진다), 요청 맥락 해시, 인증값.

## 자격 증명 관리

- **Codex 로그인**: 운영자가 Mac에서 `codex login`(ChatGPT)으로 만든다. Codex CLI가 사용자 홈에 보관하며, 저장소·서버·문서·로그로 복사하지 않는다. 폐기는 `codex logout`, 교체는 다시 로그인이다. 다른 기기로 옮길 때도 파일을 복사하지 말고 그 기기에서 새로 로그인한다.
- **API 키**: 쓰지 않는다. 환경에 있어도 Codex 실행 전에 제거된다. 저장소나 GitHub secret에 등록하지 않는다.
- **`GITHUB_TOKEN`**: 워크플로 실행마다 발급·만료된다. publish job에서만 쓰기 권한을 갖는다.
- **Mac git 자격 증명**: 운영자 keychain에 있다. 실행기는 이를 읽기만 하며 기록하지 않는다.
- **launchd 설정 파일**: `~/Library/LaunchAgents/com.contiloop.drillship-news-worker.plist`, 권한 600. PATH 외의 환경변수는 넣지 않는다.

## 민감 정보 처리

- 실행 오류 메시지(Codex CLI stderr 끝부분 포함)는 기록 파일의 `lastError`로 들어가 공개 manifest의 `readingErrors`에도 실린다. 따라서 오류 메시지·로그에 인증값이 들어갈 수 있는 코드를 넣지 않는다.
- 내부 모델명·설정·대조 과정은 화면에 노출하지 않는다.
