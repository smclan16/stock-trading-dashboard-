## 컴플라이언스 보고서 (compliance-checker)

**검증 기준일:** 2026-05-23
**검증 대상:** 06_research/ranking.md, 07_portfolio/model_portfolio.md

---

### 검증 1: 면책 문구 포함 여부

| 파일 | 면책 문구 | 판정 |
|------|---------|------|
| 06_research/ranking.md | 미포함 | **WARN** |
| 07_portfolio/model_portfolio.md | "본 포트폴리오는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 결과의 책임은 투자자 본인에게 있습니다." 포함 | **PASS** |

> WARN: ranking.md에 면책 문구 없음. 향후 추가 권장.
> model_portfolio.md는 파일 하단에 면책 문구 명시적 포함.

---

### 검증 2: 직접 투자 권유 표현 부재 확인

| 파일 | 직접 투자 권유 표현 | 판정 |
|------|----------------|------|
| 06_research/ranking.md | 없음 — 객관적 점수/지표/근거 서술 방식, "매수" 표현은 증권사 컨센서스 인용("만장일치 매수") | **PASS** |
| 07_portfolio/model_portfolio.md | 없음 — 비중/지표/방법론 서술, 면책 문구로 투자 권유 명시적 부인 | **PASS** |

> 참고: ranking.md의 "만장일치 매수"는 당사 의견이 아닌 증권사 컨센서스 인용 표현으로 직접 권유로 보기 어려우나, 인용 출처 명시 강화 권장.

---

### 검증 3: excluded_tickers / excluded_sectors 위반 여부

| 항목 | constraints.json 설정 | 포트폴리오 현황 | 판정 |
|------|---------------------|--------------|------|
| excluded_tickers | [] (없음) | 해당 없음 | **PASS** |
| excluded_sectors | [] (없음) | 해당 없음 | **PASS** |

> 이번 케이스는 제외 목록 없음 — 위반 가능성 없음.

---

### 종합 판정

| 검증 항목 | 결과 |
|---------|------|
| 면책 문구 포함 여부 | **WARN** (ranking.md 미포함) |
| 직접 투자 권유 표현 부재 | **PASS** |
| 제외 티커/섹터 위반 | **PASS** |

**종합: PASS with WARN**
- 결정적 컴플라이언스 위반 없음
- 개선 권고: ranking.md 하단에 면책 문구 추가, 컨센서스 인용 시 출처 명시
