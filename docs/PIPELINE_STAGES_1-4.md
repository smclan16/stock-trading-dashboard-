# 투자 의사결정 파이프라인 — 1~4단계 종합 문서

**대상:** 팀 내 공유 (개발자·기획·운영)
**작성일:** 2026-05-27
**프로젝트 루트:** `06.개인화추천시스템/주식매매자동화앱/`
**Top-down 파이프라인 8단계 중 1~4단계 (장기 의사결정 부분)**

---

## 0. 전체 파이프라인 흐름

```
[1단계] 투자성향 진단        →  사용자 제약 (주식비중 한도·종목·섹터·변동성 한도)
   ↓ constraints.json
[2단계] 매크로 분석·자산배분   →  레짐 판별 → 최종 주식:현금 비중
   ↓ allocation.json
[3단계] 유니버스 스크리닝     →  Top 100 종목 (5팩터 섹터중립 z-score)
   ↓ universe.json
[4단계] 투자 아이디어 발굴    →  3단계 깔때기 + 5점 평가 + 4분류
   ↓ ideas.json
[5~8단계] 종목 매칭 → 기업 리서치 → 포트폴리오 → 트레이딩 시그널
```

**의존성:**
- 1단계는 사용자 설문이 입력 (수동)
- 2단계는 1단계의 `constraints.json` 의존
- 3단계는 2단계의 `allocation.json` 의존 (레짐 가중치 적용)
- 4단계는 독립적 (병렬 가능). 단, 5단계(아이디어-종목 매칭)는 3·4단계 모두 의존

**HITL 원칙:** 트레이딩 시그널은 자동 생성되지만 실제 매매 집행은 사용자 명시 승인 후에만 가능 (8단계).

---

## 단계별 빠른 참조

| 단계 | 에이전트 | 스킬 | 실행 명령 (또는 LLM) | 핵심 산출물 |
|------|---------|------|---------------------|------------|
| 1. 투자성향 | `investment-profiler` | `investment-profiling` | LLM 설문 분석 | `01_profile/constraints.json` |
| 2. 매크로·자산배분 | `macro-allocator` | `macro-analysis` | `python3 _workspace/02_macro/fetch_macro.py` | `02_macro/allocation.json` |
| 3. 유니버스 | `universe-screener` | `universe-screening` | `python3 _workspace/03_universe/screen_full.py` | `03_universe/universe_full.json` |
| 4. 아이디어 | `idea-generator` | `idea-generation` | `python3 _workspace/04_ideas/collect_signals.py` + LLM 평가 | `04_ideas/ideas.json` |

---

## 1단계 — 투자성향 진단

### 에이전트: `investment-profiler`
**역할:** 사용자 설문 6항목을 정량 스코어링하여 투자자 유형·주식 비중 범위·포트폴리오 제약 조건 산출.

### 스코어링 로직
- 6개 항목 가중 평균 (각 0~20점 정규화):
  - 위험감내도 30% / 투자기간 20% / 손실허용폭 20% / 목표수익률 15% / 투자경험 10% / 유동성 5%
- 총점(0~100) → 5단계 유형 분류

### 투자자 유형 5단계
| 유형 | 점수 | 주식 비중 한도 (equity_pct_min ~ max) |
|------|------|------|
| 안정형 | 0~39 | 20% ~ 100% |
| 안정추구형 | 40~59 | 40% ~ 100% |
| 위험중립형 | 60~74 | 60% ~ 100% |
| 적극투자형 | 75~89 | 100% ~ 150% |
| 공격투자형 | 90~100 | 120% ~ 200% |

### 입력 / 출력
- 입력: `_workspace/00_input/user_survey.md` (수동)
- 출력:
  - `_workspace/01_profile/investment_profile.md` — 유형·근거·권장 비중
  - `_workspace/01_profile/constraints.json` — 후속 단계용 제약 JSON (`equity_pct_min`, `equity_pct_max`, `max_single_stock_pct`, `max_sector_pct`, `max_annual_volatility`, `excluded_tickers`, `excluded_sectors`, `esg_filter`)

### 에러 핸들링
- 누락 항목 → 중립값(10점) 처리 + "누락 N개" 명시
- 모순 응답(낮은 손실허용 + 높은 목표수익률) → 보수적 유형으로 분류 + 모순 경고

### 현 상태 (2026-05-27)
- 적용된 사용자: 적극투자형 (89.25점) → equity_pct 100~150%
- 메모: 단기 투자기간 + 극단 위험 선호 불일치 경고 (재확인 권장)

---

## 2단계 — 매크로 분석·자산배분

### 에이전트: `macro-allocator`
**역할:** VIX·미국채 10년물 등 매크로 지표를 수집·해석하여 시장 레짐을 판별하고, 1단계 제약 내에서 최종 주식·현금 비중을 결정.

### 데이터 소스 (전부 FRED LIVE, 자동화 완료)
| 키 | 시리즈 ID | 역할 | 갱신 주기 |
|---|---|---|---|
| `vix` | VIXCLS | 핵심 — CBOE VIX 지수 | 일별 |
| `us10y` | DGS10 | 핵심 — 미국채 10년물 금리(%) | 일별 |
| `fed_funds` | FEDFUNDS | 모니터링 — Fed Funds 실효금리 | 월별 |
| `unemployment` | UNRATE | 모니터링 — 실업률 | 월별 |
| `cpi` | CPIAUCSL | 모니터링 — CPI 지수 (YoY 자동 변환) | 월별 |
| `pce` | PCEPI | 모니터링 — PCE 지수 (YoY 자동 변환) | 월별 |

- 어댑터: `_workspace/lib/datasource.py` → `MacroData` 클래스
- 키: `_workspace/.env`의 `FRED_API_KEY` (무료 발급, https://fred.stlouisfed.org/docs/api/api_key.html)
- **이전엔 LLM이 WebFetch로 매번 수동 리서치 → FRED 단일 소스 자동화로 격상**

### 레짐 판별 — VIX × US10Y 3×3 매트릭스

VIX 구간: Low (<20) / Mod (20~30) / High (≥30)
US10Y 구간: Low (<3.5%) / Mod (3.5%~4.5%) / High (≥4.5%)

| | US10Y Low | US10Y Mod | US10Y High |
|---|---|---|---|
| **VIX Low** | 1.0 (초위험선호) | 0.8 (위험선호) | **0.5 (중립)** |
| **VIX Mod** | 0.7 (다소 위험선호) | 0.5 (중립) | 0.3 (다소 위험회피) |
| **VIX High** | 0.3 (다소 위험회피) | 0.2 (위험회피) | 0.0 (초위험회피) |

### 자산배분 공식
```
equity_pct = equity_pct_min + (equity_pct_max − equity_pct_min) × W_macro
cash_pct   = 100 − equity_pct   (음수 = 신용매수/차입)
```

### 실행
```bash
python3 _workspace/02_macro/fetch_macro.py
```
→ FRED 6개 시리즈 자동 수집 → 레짐 판별 → `allocation.json` + `macro_dashboard.md` 자동 생성 (~10초)

### 현 상태 (2026-05-27)
- VIX 16.59 (Low) × US10Y 4.57% (High) → W_macro = 0.5 (중립)
- 적극투자형(100~150%) × 0.5 → **주식 125% / 현금 -25%** (레버리지 25%)

### 산출물
- `_workspace/02_macro/macro_dashboard.md` — 지표 표·레짐 판별·근거
- `_workspace/02_macro/allocation.json` — 최종 비중 + basis(원시값) 구조

---

## 3단계 — 유니버스 스크리닝

### 에이전트: `universe-screener`
**역할:** 한국 전 종목에서 계량 팩터로 Top 100을 선별. 5팩터 섹터중립 z-score + 레짐 가중치 적용.

### 데이터 소스 (전부 LIVE)
| 소스 | 어댑터 | 역할 |
|---|---|---|
| KRX Open API | `KRXMarket` | 종목마스터·시총·**20일/60일 거래대금**·거래정지·관리종목·종가·상장주식수 |
| FnSpace `account` | `FinancialsData` | ROE·ROA·영익률·부채비율·매출/영익 증가율·EPS·BPS·EV/EBITDA |
| FnSpace `consensus-earning-fiscal` | `ConsensusData` | **컨센서스 존재 필터** (영업이익 추정 non-null) |
| FnSpace `consensus-price` | `ConsensusData.revision_1m` | E610360 목표주가괴리율 시계열 → **목표주가 1M 변화율 유도** |
| FnSpace `consensus-forward` | `ConsensusData.revision_1m` | E211570 Fwd ROE 시계열 → **Fwd ROE 1M 변화율** (EPS Fwd 12M 항목 null 응답 → ROE 대체) |
| Open DART | `DartSector` | induty_code → 광의 섹터(KSIC 2자리) → 섹터중립 z-score |

### 하드필터 (4단계)
1. 거래정지 제외 (거래량 0)
2. 관리종목 제외 (소속부 "관리" 포함)
3. 20일 평균 거래대금 ≥ 1억원
4. **애널리스트 컨센서스 존재** (영업이익 추정값 non-null)

비용 경계: KRX 하드필터(무료) 통과 후 **시총 상위 `MAX_FNSPACE`(기본 1000)** 종목만 FnSpace 조회.

### 5팩터 정의 (모두 섹터중립 z-score, ±5σ winsor)

| # | 팩터 | 산식 | 비고 |
|---|---|---|---|
| 1 | **밸류** | mean(z(−PER), z(−PBR), z(−EV/EBITDA)) | 기존 그대로 |
| 2 | **퀄리티** | z(ROE + ROA − 부채비율/100 + 영업이익률) | 기존 그대로 |
| 3 | **목표가↑(1M)** | z( (TP_now / TP_1M − 1) × 100 ), TP = 종가×(1+괴리율/100) | **신규** |
| 4 | **Fwd ROE↑(1M)** | z( (ROE_F12M_now / ROE_F12M_1M − 1) × 100 ) | **신규** (EPS Fwd 12M 대체) |
| 5 | **거래대금↑(3M→1M)** | z( (최근 1M 평균거래대금 / 3M전 1M 평균거래대금 − 1) × 100 ) | **신규** |

### 레짐별 가중치 (5팩터 재설계)
| 레짐 | 밸류 | 퀄리티 | 목표가↑ | Fwd ROE↑ | 거래대금↑ |
|------|------|--------|---------|----------|-----------|
| 위험선호 | 10% | 15% | 25% | 25% | 25% |
| 중립 | 20% | 20% | 20% | 20% | 20% |
| 위험회피 | 30% | 30% | 15% | 15% | 10% |

위험선호 = 리비전·거래대금(모멘텀성) 75% 강조 / 위험회피 = 밸류·퀄리티(펀더멘털) 60% 강조.

### 실행
```bash
python3 _workspace/03_universe/screen_full.py
```
→ `universe_full.{md,json}` 생성 → 검증 후 `universe.json` 으로 promote (수동)
- 기본 MAX_FNSPACE=1000, TOPN=100. 비용·시간 조정 시 모듈 패치로 변경 가능
- 소요: ~10분 (FnSpace 호출 분량에 비례)

### 산출물
- `_workspace/03_universe/universe_full.json` — 100개 종목 + 점수 + 메트릭
- `_workspace/03_universe/universe_full.md` — 사람 읽기용 테이블

### 한계 (보수적 처리)
- 거래정지 = 거래량 0 근사
- 목표주가 = 종가 × (1 + 괴리율/100) 유도 (직접 목표가 item 미사용)
- EPS Fwd 12M 항목 null → ROE Fwd 12M 변화율로 대체 (자본 변동 작을 때 강한 양의 상관)
- DART 업종 표본<5인 섹터는 전역 z-score 폴백

---

## 4단계 — 투자 아이디어 발굴

### 에이전트: `idea-generator`
**역할:** **장기 투자 아이디어**를 약한 신호 단계에서 포착해 3단계 검증을 거쳐 4분류로 분류. 핵심 후보만 다음 단계로 전달.

### 핵심 철학
> "단기 모멘텀 추격이 아닌, **3년 이상 지속 가능한 구조적 변화**를 약한 신호 단계에서 포착하여 자금이 본격 유입되기 전에 선점."

### 3단계 깔때기
```
[1단계 약한 신호 발굴]  ─►  [2단계 구조적 수요 확인]  ─►  [3단계 실제 자금 흐름]
 얼리어답터·검색·VC·논문      시장규모·정책·공급망 변화        ETF Flow·Capex·실적 컨센서스
  (자동 수집)                 (LLM 리서치)                    (LLM + FnSpace LIVE)
```

### 1단계 약한 신호 — 자동 수집 (10개 신호, 한국 보강)

> **표 읽는 법**: "작동 상태" 컬럼이 실제 동작 여부. "키" 컬럼은 등록 필요 여부.

| 차원 | 지표 | 출처 | 윈도우 | 작동 상태 | 키 |
|---|---|---|---|---|---|
| 연구·기술 (글로벌) | arXiv 논문 증가율 | arXiv API | 30d vs 180d | ✅ LIVE | 불필요 |
| 연구·기술 (글로벌) | Google Patents 출원 증가율 | patents.google.com (xhr) | 6M vs 24M | ❌ **IP 차단 진행** | 불필요(비공식) |
| 개발자 (글로벌) | GitHub 신규 repo 증가율 | GitHub Search API | 30d vs 180d | ✅ LIVE | `GITHUB_TOKEN` (옵션, 60→5000 req/h) |
| 얼리어답터 (글로벌) | HackerNews 게시물 증가율 | HN Algolia API | 30d vs 180d | ✅ LIVE | 불필요 |
| 대중 (글로벌) | Wikipedia EN Pageviews | Wikimedia REST API | 4w vs 12w | ✅ LIVE | 불필요 |
| **대중 (한국)** | **Wikipedia KO Pageviews** | Wikimedia REST API | 4w vs 12w | ✅ LIVE | 불필요 |
| **미디어 (한국)** | **네이버 뉴스 mention** | openapi.naver.com | 30d vs 180d | ✅ LIVE | `NAVER_CLIENT_ID`/`SECRET` |
| 사업화 (미국) | SEC EDGAR 10-K mention | efts.sec.gov | 90d vs 365d | ✅ LIVE (retry 패치 후 100%) | 불필요 |
| 정책 (미국) | federalregister.gov mention | 공식 API | 90d vs 365d | ✅ LIVE | 불필요 |
| 정책 (한국, 옵션) | 국회 의안 발의 | open.assembly.go.kr | 6M vs 24M | ⚪ 미사용 (사용자 결정) | `DATA_GO_KR_KEY` → 대신 LLM WebFetch 보강 |

**현재 작동: 10개 중 8개 LIVE / 1개 차단(Patents) / 1개 미사용(국회 의안).** Patents는 USPTO 공식 API로 교체 시 복구 가능.

- 어댑터: `_workspace/lib/datasource.py` → `WeakSignals` 클래스
- 키워드 큐레이션: `_workspace/04_ideas/keywords.json` (없으면 `collect_signals.py`의 DEFAULT_KEYWORDS 10개 테마)
- 네이버 키워드는 매칭 수가 적은 구체적 표현 사용 (예: "AI반도체" → "HBM 수출", "양자컴퓨터" → "양자컴퓨터 R&D")

### 2단계 구조적 수요 — LLM 리서치
LLM이 각 테마에 대해 다음 질문 검증 (WebFetch/WebSearch):
1. 시장규모(TAM) + CAGR: 3년 이상 두 자릿수 성장?
2. 공급망/생태계 변화: 영구적인가 일시적 붐인가?
3. 정책 모멘텀: 주요국(미·중·EU·한) 정책·예산이 가속?
4. 반대 가설: 이 테마를 죽일 수 있는 변수?

### 3단계 실제 자금 흐름 — LLM + FnSpace LIVE
1. ETF·펀드 자금 유입 (etf.com)
2. 기업 Capex (어닝콜·DART/SEC 공시)
3. 실적 컨센서스 변화 (한국 종목은 FnSpace consensus 이미 LIVE)

### 5점 평가 (각 0~5, 총 25점)

| 항목 | 정의 | 자동 신호 | LLM 리서치 보강 |
|---|---|---|---|
| **Durability** | 3년 이상 지속 가능한 구조적 변화 | Patents, arXiv | 산업 구조 영구 변화 |
| **Capital Inflow** | ETF·VC·Capex 실제 자금 유입 | HN, SEC EDGAR | ETF Flow, Crunchbase |
| **Verifiability** | 12개월 내 상장사 실적 반영 가능 | SEC EDGAR, Wiki KO, 네이버 뉴스 | FnSpace 컨센서스 |
| **Earliness** | 아직 대중에 덜 알려짐 (역지표) | GitHub, HN, arXiv | — |
| **Policy Momentum** | 글로벌/국내 정책이 지원 | federalregister.gov (+옵션: 국회 의안) | korea.kr·산업부·EU Commission |

### 4분류 임계값
| 분류 | 조건 | 의미 |
|---|---|---|
| **핵심 장기 테마 후보** | total ≥ 18 AND 모든 항목 ≥ 3 AND Durability ≥ 4 | 즉시 idea-stock-matching 진행 |
| **관찰 리스트** | 13~17점 OR (total ≥ 18인데 Durability<4) | 다음 사이클 재평가 |
| **검증 부족** | Earliness ≥ 4 AND (Capital ≤ 2 OR Verifiability ≤ 2) | 초기성 강함, 자금/실적 신호 추가 확인 |
| **아직 약한 아이디어** | total < 10 OR Durability ≤ 1 | 보류, 다음 사이클 재수집 |

**분류 우선순위:** "검증 부족" 조건 충족 시 total과 무관하게 검증부족 분류 (초기성 보호).

### 실행 흐름 (사용자 ↔ Claude 협업)
```bash
# 1단계 자동 수집 (~5분, 비용 0)
python3 _workspace/04_ideas/collect_signals.py
```
→ `signals_raw.json` 생성

이후 Claude가:
2. signals_raw.json 읽고 2·3단계 WebFetch 리서치
3. 5점 평가 + 4분류 → `ideas.json` 작성
4. 사람 읽기용 `idea_cards.md` 4 카테고리 출력
5. 사용자 확인 후 핵심 후보만 `idea-stock-matching` 단계로 전달

### 현 상태 (2026-05-27, v2)
- 10개 테마 수집 완료
- **핵심 장기 테마 후보 3개**: 데이터센터 전력(21점), SMR·소형원전(20점), 양자 컴퓨팅(20점)
- 검증 부족 0개
- 관찰 리스트 7개

---

## 데이터 소스 종합 요약

### LIVE 자동화 (모두 정상 작동 중, 2026-05-27 확인)
| 단계 | 소스 | 키 (등록 필요 여부) |
|---|---|---|
| 2단계 | **FRED** (VIXCLS, DGS10, FEDFUNDS, UNRATE, CPIAUCSL, PCEPI) | `FRED_API_KEY` (필요) |
| 3단계 | **KRX Open API** (시장 데이터) | `KRX_API_KEY` (필요) |
| 3단계 | **FnSpace** (account, consensus) | `FNSPACE_API_KEY` (필요, 종목당 과금) |
| 3단계 | **Open DART** (업종 분류) | `DART_API_KEY` (필요) |
| 4단계 | **arXiv API** (논문) | 불필요 |
| 4단계 | **Wikipedia EN/KO Pageviews** | 불필요 |
| 4단계 | **GitHub Search** | `GITHUB_TOKEN` (옵션, 60→5000 req/h) |
| 4단계 | **HackerNews Algolia** | 불필요 |
| 4단계 | **SEC EDGAR full-text** (10-K) | 불필요 |
| 4단계 | **federalregister.gov** | 불필요 |
| 4단계 | **네이버 뉴스 검색 API** | `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` (필요) |

### 옵션 / 보류
| 소스 | 상태 | 대응 |
|---|---|---|
| Google Patents (xhr) | ⚠️ IP 차단 진행 | LLM 도메인 지식으로 Durability 보강. 필요시 USPTO 공식 API 발급 |
| pytrends (Google Trends) | ⚠️ 미설치 (best-effort) | Wikipedia + arXiv가 대중·연구 신호 커버 |
| 국회 의안 (open.assembly.go.kr) | ⚠️ 키 미발급 | LLM이 korea.kr·산업부·과기부 WebFetch 보강 |
| Finnhub 한국주식 | ❌ 무료 티어 미지원 | (사용 안 함) |

### `.env` 키 등록 현황 (2026-05-27)
- ✅ FRED_API_KEY, KRX_API_KEY, DART_API_KEY, FNSPACE_API_KEY
- ✅ GITHUB_TOKEN (5000 req/h 적용)
- ✅ NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
- ❌ DATA_GO_KR_KEY (사용자 결정으로 자동화 제외, LLM 보강)

---

## 산출물 위치 종합

```
_workspace/
├── 00_input/                        # 사용자 입력
│   └── user_survey.md
├── 01_profile/                      # 1단계
│   ├── constraints.json             ← 후속 단계 핵심 입력
│   └── investment_profile.md
├── 02_macro/                        # 2단계
│   ├── allocation.json              ← 후속 단계 핵심 입력 (regime, w_macro)
│   ├── macro_dashboard.md
│   └── fetch_macro.py
├── 03_universe/                     # 3단계
│   ├── screen_full.py               ← 메인 스크리너
│   ├── universe_full.{md,json}      ← 검증 전 결과
│   ├── universe.json                ← 검증 후 promote (수동)
│   └── DATA_SOURCES.md
├── 04_ideas/                        # 4단계
│   ├── collect_signals.py           ← 약한 신호 자동 수집
│   ├── signals_raw.json             ← 10개 신호 raw
│   ├── ideas.json                   ← 5점 평가 + 4분류 구조화
│   └── idea_cards.md                ← 사람 읽기용 카테고리별 카드
└── lib/
    ├── datasource.py                ← 모든 데이터 어댑터 (MacroData, KRXMarket, FinancialsData, ConsensusData, DartSector, WeakSignals)
    └── config.py                    ← .env 로더
```

---

## 최근 주요 변경사항 (2026-05-26 ~ 27)

### 2단계 (매크로)
- **LLM 수동 WebFetch → FRED 단일 소스 자동화**
- `MacroData` 클래스 + `fetch_macro.py` 신규
- VIX도 FRED `VIXCLS`로 수집 (yfinance 의존성 불필요)

### 3단계 (유니버스) — 4팩터 → 5팩터 전환
- 모멘텀 12-1M, 성장 YoY → **목표가 1M 변화율 + Fwd ROE 1M 변화율 + 거래대금 3M→1M 증가율**로 교체
- 밸류·퀄리티는 기존 그대로
- 레짐 가중치 5팩터로 재설계
- EPS Fwd 12M 항목 null 응답 → ROE Fwd 12M 변화율로 대체 (검증)

### 4단계 (아이디어) — 단기 트렌드 → 장기 깔때기 모델 전환
- Google Trends + 뉴스 + 매크로 일관성 → **3단계 깔때기 + 5점 평가 + 4분류**
- 자동 신호 1~3개 → **10개 다차원** (글로벌 6 + 한국 3 + 옵션 1)
- 한국 신호 추가: Wikipedia KO, 네이버 뉴스
- SEC EDGAR retry 패치 (인용구→평문 폴백)로 측정률 향상
- 네이버 키워드 좁히기로 truncated 8/10 → 4/10 개선

---

## 운영 권장 사이클

| 주기 | 작업 |
|---|---|
| **신규 사용자** | 1단계 1회 수행 → `constraints.json` 생성 |
| **일별** | 2단계 자동 갱신 (매크로 지표는 일별 변동) |
| **주별** | 3단계 재수집 (FnSpace 비용 고려 시 주 1회) |
| **월별~분기별** | 4단계 재수집 + 5점 평가 갱신. "관찰 리스트"/"검증 부족" 재평가 |
| **변경 시점** | 1단계는 사용자 상황 변화 시 (수입·자산·목표 변경) 재수행 |

---

## 알려진 한계와 향후 개선 후보

1. **3단계**: Google Patents 자동화 불가 → USPTO 공식 API (무료 키 발급) 검토
2. **3단계**: 정확한 수정주가 부재 → 상장주식수로 액면분할·증자 보정 (현재 winsorize ±5σ)
3. **4단계**: 네이버 일부 키워드 여전히 truncated (HBM 수출 등) → 더 좁은 키워드 또는 다중 키워드 합산
4. **4단계**: 한국 정책 자동화 (국회 의안 API)
5. **4단계**: EU 정책 자동화 부재 → LLM WebFetch만 의존
6. **전체**: 데이터 검증 단계(`data-validator`, `logic-validator`) 자동화 통합

---

## 참고: 에이전트 ↔ 스킬 ↔ 코드 관계

| 에이전트 (페르소나) | 스킬 (실행 매뉴얼) | 코드 어댑터 |
|---|---|---|
| `investment-profiler` | `investment-profiling` | (LLM 전담) |
| `macro-allocator` | `macro-analysis` | `lib/datasource.py::MacroData`, `02_macro/fetch_macro.py` |
| `universe-screener` | `universe-screening` | `lib/datasource.py::KRXMarket/FinancialsData/ConsensusData/DartSector`, `03_universe/screen_full.py` |
| `idea-generator` | `idea-generation` | `lib/datasource.py::WeakSignals`, `04_ideas/collect_signals.py` |

- **에이전트** = LLM의 페르소나·책임 정의 (`.claude/agents/*.md`)
- **스킬** = 단계별 실행 매뉴얼·데이터 소스·산출물 포맷 (`.claude/skills/*/SKILL.md`)
- **코드** = 실제 API 호출·계량 계산 (`_workspace/lib/`, `_workspace/0X_*/`)

---

*문서 관리: 변경 시 `CLAUDE.md`의 변경 이력 표에도 반영*
