# 외부 데이터 소스 연동 현황

최종 점검: 2026-05-26 / 어댑터: `_workspace/lib/datasource.py` / 진단: `python3 lib/datasource.py`

## 키 관리
- 키는 `_workspace/.env` (FNSPACE_API_KEY, FINNHUB_API_KEY, KRX_API_KEY, DART_API_KEY)에 저장, 코드/산출물에 하드코딩 금지.
- 로더: `_workspace/lib/config.py` (환경변수 우선 > .env). 상태: `python3 lib/config.py` (마스킹).
- ⚠️ 작업 폴더가 Google Drive 동기화 폴더 → `.env`도 클라우드 동기화됨에 유의.

## 소스별 가용성 (LIVE / DEFERRED)

| 소스 | 카테고리 | 상태 | 제공 데이터 |
|------|----------|------|-------------|
| **KRX Open API** | 일별매매정보·종목기본정보 | ✅ LIVE | 종목마스터·시장구분·**시총·20일거래대금·종가(모멘텀)·상장주식수·관리종목(소속부)** |
| **FnSpace** | `account` (재무) | ✅ LIVE | ROE·ROA·영업이익률·부채비율·매출/영익 증가율(YoY)·EV/EBITDA·**EPS·BPS** |
| **FnSpace** | `consensus-*` | ✅ LIVE | **컨센서스 존재(필터)**·Fwd ROE/EV-EBITDA·목표주가괴리율·리비전·투자의견 |
| **Open DART** | corpCode·기업개황 | ✅ LIVE | **종목별 업종(induty_code/KSIC)** → 광의 섹터 → 섹터중립 z-score |
| FnSpace | `stock_price`/`stock_list` | ⛔ 불요 | 권한없음이나 **KRX가 대체**(시총/거래대금/마스터) |
| Finnhub | 한국주식 | ❌ DEFERRED | 무료티어 미지원(403) |
| Finnhub | 미국주식 | ✅ LIVE | PER/PBR/ROE/추천(향후 미국·글로벌 유니버스용) |
| KRX | pykrx(스크래핑) | ❌ 불요 | 비한국 IP 차단 — **공식 Open API로 대체** |

### 인증/제약
- KRX: `https://data-dbg.krx.co.kr/svc/apis`, **HTTP 헤더 `AUTH_KEY`**, 서비스별 '활용신청' 승인 필요(승인됨). 일별매매 1콜=그날 전종목.
- FnSpace: 요청당 **최대 10종목**(어댑터 자동 청크), 종목코드 "A"+6자리, **종목당 과금**(consensus).
- DART: `https://opendart.fss.or.kr/api`, `crtfc_key`, 종목당 1콜(기업개황). corpCode/업종은 `lib/.cache/`에 캐시 → 재실행 시 즉시.
- 환경 시계가 KRX 실제 가용일보다 앞설 수 있어 `latest_trading_date()`가 역탐색.

## 전체 파이프라인 (현재 canonical) — `screen_full.py`

실행: `python3 _workspace/03_universe/screen_full.py` → `universe_full.{md,json}` → 검증 후 `universe.json` 승격.

**하드필터(요청 스펙)**: ① 거래정지 제외(거래량0) ② 관리종목 제외(소속부) ③ 20일평균거래대금 1억 미만 제외 ④ 애널리스트 컨센서스 존재(추정값 non-null) — 코스피/코스닥 보통주.
**4팩터 섹터중립 z-score(±5σ winsor) + 레짐 가중치** (섹터 = DART 업종, 표본<5 섹터는 전역 폴백):
- 밸류 = avg(z(−PER=종가/EPS), z(−PBR=종가/BPS), z(−EV/EBITDA))
- 퀄리티 = z(ROE + ROA − 부채비율/100 + 영업이익률)
- 모멘텀 = z(12−1M 가격수익률, 상장주식수 보정)
- 성장 = z(매출·영업이익 증가율 YoY)

**비용 경계**: fnspace/DART 과금·콜 → KRX 하드필터(무료) 후 시총 상위 `MAX_FNSPACE`(기본 1000)만 컨센서스/재무 조회, DART 업종은 컨센서스 통과분만 조회(캐시).

## 남은 개선점 (확장점)
- **거래정지**: 전용 플래그 부재 → 거래량 0 근사. KRX 별도 상태 서비스 확보 시 정밀화.
- **모멘텀 수정주가**: 상장주식수로 분할/병합 보정(증자·합병 잔여분은 [-95%,1500%] winsorize). 정식 수정주가 소스 확보 시 대체 가능.
- **DART 업종 매핑률**: 약 98%(미상장/매핑누락분은 미분류→전역 폴백). KSIC 2자리→광의 섹터 매핑은 `datasource.DartSector.KSIC2`에서 조정 가능.
