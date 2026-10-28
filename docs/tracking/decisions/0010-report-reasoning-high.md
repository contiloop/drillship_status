# 0010. Fleet Status 보고서만 reasoning high로 읽는다

## 맥락

검증은 화면 안전만 보므로, 읽기 정확도는 Codex 설정에 달려 있다. 2026-10-28 운영 데이터에서 Noble Voyager–Shell 계약의 기간·금액이 빠졌다. 같은 Noble 보고서(2026-10-26 판)를 같은 입력으로 reasoning `medium`과 `high`로 각각 3번 읽혀, 원문에 있는 계약 5건(Voyager–Shell Sep-26~Sep-30·6억 600만 달러, Venturer–Shell Q4-27~Q3-31·6억 600만 달러, Venturer–Planet One Oct-26~Nov-26·하루 43만 달러, Faye Kozack–Tullow Mid 2027~Q1 2028, BlackHawk–Apache Q4 2027·2,500만 달러·50일)이 맞게 나왔는지 비교했다.

- `medium`: 15건 중 11건(5/5, 4/5, 2/5). 3번 모두 겹침 재확인이 필요했다.
- `high`: 15건 중 15건. 3번 중 2번은 재확인 없이 끝났다.
- 걸린 시간은 한 번에 70~114초로 비슷했다.

## 결정

수집한 Fleet Status 보고서를 읽을 때만 `high`를 쓴다. 기사, 저장 결과 보완, 제목 선별은 `medium`을 유지한다.

## 대안

- **전부 `high`**: 기사는 짧고 계약 한두 건이라 `medium`에서 문제가 관찰되지 않았다. 기사 수가 보고서보다 훨씬 많아 사용 한도를 더 빨리 쓴다.
- **전부 `medium` 유지**: 보고서는 회사당 분기 1회 정도라 비용 차이가 작은데, 틀린 결과는 다음 보고서까지 한 분기 동안 화면에 남는다.
- **코드로 결과 검사 추가**: 화면 안전만 본다는 결정과 충돌한다.

## 결과

- 보고서 1건 읽기가 사용 한도를 조금 더 쓴다.
- 비교는 한 보고서·3회씩이라 표본이 작다. 다른 회사 보고서에서 `high`도 틀리면 그때 다시 본다.
- 설정은 `reading_service.py`의 `REPORT_REASONING_EFFORT`와 `reasoning_effort_for`에 있다.
