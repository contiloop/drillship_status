# Drillship Status

Offshore Drillship Fleet Status & Pricing Intelligence 대시보드

## 실행 방법

```bash
npm install
npm run dev
```

브라우저에서 `http://localhost:3000` 접속

## 기능

### Dashboard
- **KPI 카드**: Total Fleet, Utilization, Avg Dayrate, Warm/Cold Stacked 현황
- **Average Dayrate 차트**: Generation별 또는 Company별 평균 Dayrate 비교
- **Asset Composition 차트**: Generation별 또는 Company별 선박 수 비교
- **Fleet Gantt**: 선박별 계약 타임라인 시각화

### README 탭
- JSON 스키마 가이드
- 필드 레퍼런스 테이블
- Notes & Tips

## 사용 팁

- JSON 파일은 Dashboard 탭에서 업로드/다운로드 가능
- 데이터는 로컬 브라우저에 저장되며, 다른 기기와 동기화되지 않음
- 중요한 데이터는 Export JSON으로 백업
- Dayrate 단위는 USD/일 기준
- 차트에서 **BY GENERATION / BY COMPANY** 버튼으로 뷰 전환 가능
- 차트 바에 커서를 올리면 상세 정보 확인 가능:
  - Company별 Utilization (가동률)
  - 최고/최저 Dayrate 선박

## Utilization 기준 (시장 사이클)

| 가동률 | 상태 | 설명 |
|--------|------|------|
| **95%+** | Super Cycle (분홍) | 초호황, 공급 부족. 모든 배가 계약 중이며 IOC들이 2-3년 뒤 물량 선점 경쟁. Dayrate $60만+ |
| **85-95%** | Seller's Market (녹색) | 공급자 우위. 85% 매직넘버를 넘으면 Dayrate 급상승. 시추업체 고단가 장기계약 선호 |
| **75-85%** | Balanced (주황) | 균형/전환기. 점유율 경쟁으로 Dayrate 상승 제한. 유가가 버텨주느냐가 관건 |
| **75% 미만** | Buyer's Market (빨강) | 수요자 우위, 불황. 공급과잉. Dayrate가 OPEX 수준으로 하락. Cold Stack/Scrapping 증가 |

## JSON 데이터 형식

```json
[
  {
    "id": "deepwater-titan",
    "name": "Deepwater Titan",
    "company": "Transocean",
    "generation": "8G",
    "status": "Active",
    "yearBuilt": 2023,
    "contracts": [
      {
        "id": "deepwater-titan-1",
        "vesselId": "deepwater-titan",
        "startDate": "2023-04-01",
        "endDate": "2028-04-01",
        "dayRate": 462000,
        "client": "Chevron",
        "region": "USGOM",
        "status": "Firm"
      }
    ]
  }
]
```

## 필드 레퍼런스

| Field | Type | Allowed Values |
|-------|------|----------------|
| company | string | Transocean, Valaris, Noble, Seadrill |
| generation | string | 6G, 7G, 7G+, 8G |
| status (ship) | string | Active, Idle, Warm-Stacked, Cold-Stacked |
| status (contract) | string | Firm, Option |
| dayRate | number | Integer (e.g. 462000) |
| client | string | e.g. Chevron, Petrobras, Shell, bp |
| region | string | e.g. USGOM, Brazil, India, Australia |
| dates | string | YYYY-MM-DD format |

## Notes

- **Noble Guyana 4척** (Tom Madden, Sam Croft, Don Taylor, Bob Douglas): ExxonMobil 계약으로, 매년 3월과 9월에 시장 상황에 따라 dayRate가 조정됨
