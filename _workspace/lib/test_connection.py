#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""외부 데이터 API 연결 검증 스크립트.

키가 주입되면(fnspace/finnhub) 각 엔드포인트를 1종목(삼성전자 005930) 기준으로 호출하여
- 연결 성공/실패(PASS/FAIL)
- 구독 범위(어떤 카테고리가 데이터를 반환하는지)
- 실제 응답 컬럼/샘플값
을 보고한다. KRX(pykrx) 직접 접근 가능 여부도 함께 진단한다.

실행: python3 _workspace/lib/test_connection.py
"""
import sys, os, warnings, datetime
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

SAMPLE = "005930"      # 삼성전자
SAMPLE2 = "035720"     # 카카오 (코스피) — 다종목 동작 확인
LAST_YEAR = str(datetime.datetime.now().year - 1)


def line(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def show_df(df, n=3):
    if df is None:
        print("  -> None (데이터 없음/실패)")
        return False
    try:
        print(f"  -> rows={len(df)}, cols={list(df.columns)}")
        if len(df):
            print(df.head(n).to_string().replace("\n", "\n  "))
        return len(df) > 0
    except Exception as e:
        print("  -> 출력 오류", e)
        return False


def test_fnspace():
    line("[1] FnSpace 연결 테스트")
    key = config.get("FNSPACE_API_KEY")
    if not key:
        print("  SKIP: FNSPACE_API_KEY 미설정")
        return
    from fnspace import FnSpace
    fs = FnSpace(key)
    results = {}

    # 1) 종목 리스트 (KOSPI+KOSDAQ) — 유니버스 모집단 + 시장구분/상태필드 확인
    print("\n-- (a) stock_list (mkttype=4: KOSPI+KOSDAQ) --")
    try:
        df = fs.get_data(category="stock_list", mkttype="4")
        results["stock_list"] = show_df(df, 5)
        if df is not None and len(df):
            print("  [전체 컬럼]", list(df.columns))
    except Exception as e:
        print("  EXC", type(e).__name__, str(e)[:200]); results["stock_list"] = False

    # 2) 재무(account) — ROE/ROA/부채비율/영익률/EV-EBITDA/성장률
    print("\n-- (b) account 재무비율 (005930, 직전연도) --")
    fin_items = ["M211500","M211600","M211000","M221100","M231000","M231400","M331030","M122700","M121000"]
    try:
        df = fs.get_data(category="account", code=[SAMPLE, SAMPLE2], item=fin_items,
                         consolgb="M", annualgb="A", from_year=LAST_YEAR, to_year=LAST_YEAR,
                         kor_item_name=True)
        results["account"] = show_df(df)
    except Exception as e:
        print("  EXC", type(e).__name__, str(e)[:200]); results["account"] = False

    # 3) 주가/거래대금/거래정지/시총 (stock_price)
    print("\n-- (c) stock_price 20일평균거래대금/거래정지/시총 (005930) --")
    sp_items = ["S106410","S120200","S102100","S100300"]  # 20일평균거래대금, 거래정지구분, 시총, 수정주가
    today = datetime.datetime.now().strftime("%Y%m%d")
    frm = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    try:
        df = fs.get_data(category="stock_price", code=[SAMPLE], item=sp_items,
                         from_date=frm, to_date=today, kor_item_name=True)
        results["stock_price"] = show_df(df)
    except Exception as e:
        print("  EXC", type(e).__name__, str(e)[:200]); results["stock_price"] = False

    # 4) 컨센서스 존재 여부 (consensus-earning-fiscal)
    print("\n-- (d) consensus-earning-fiscal 추정실적 (005930) --")
    try:
        df = fs.get_data(category="consensus-earning-fiscal", code=[SAMPLE],
                         item=["E121500","E122700","E211500"],  # 영업이익/순이익/ROE 추정
                         consolgb="M", annualgb="A",
                         from_year=str(datetime.datetime.now().year),
                         to_year=str(datetime.datetime.now().year), kor_item_name=True)
        results["consensus-earning-fiscal"] = show_df(df)
    except Exception as e:
        print("  EXC", type(e).__name__, str(e)[:200]); results["consensus-earning-fiscal"] = False

    # 5) 컨센서스 투자의견/목표주가 (consensus-price)
    print("\n-- (e) consensus-price 투자의견&목표주가 (005930) --")
    try:
        df = fs.get_data(category="consensus-price", code=[SAMPLE], item=["E610100","E612500"],
                         from_date=frm, to_date=today, kor_item_name=True)
        results["consensus-price"] = show_df(df)
    except Exception as e:
        print("  EXC", type(e).__name__, str(e)[:200]); results["consensus-price"] = False

    line("[FnSpace 구독범위 요약]")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL/EMPTY':10s} {k}")


def test_finnhub():
    line("[2] Finnhub 연결 테스트")
    key = config.get("FINNHUB_API_KEY")
    if not key:
        print("  SKIP: FINNHUB_API_KEY 미설정")
        return
    import finnhub
    cli = finnhub.Client(api_key=key)
    for sym in ["005930.KS", "AAPL"]:
        print(f"\n-- recommendation_trends({sym}) --")
        try:
            r = cli.recommendation_trends(sym)
            print("  ->", (r[:1] if r else r))
        except Exception as e:
            print("  EXC", type(e).__name__, str(e)[:200])
        print(f"-- company_basic_financials({sym}, 'all') 일부 --")
        try:
            m = cli.company_basic_financials(sym, "all").get("metric", {})
            keys = ["peTTM","pbAnnual","roeTTM","roaTTM","epsTTM"]
            print("  ->", {k: m.get(k) for k in keys} if m else "빈 metric")
        except Exception as e:
            print("  EXC", type(e).__name__, str(e)[:200])


def test_krx():
    line("[3] KRX(pykrx) 직접 접근 진단 (참고)")
    try:
        from pykrx import stock
        df = stock.get_market_cap_by_ticker("20250902", market="KOSPI")
        if df is not None and len(df):
            print(f"  PASS: KRX 직접 접근 가능 (KOSPI {len(df)}종목)")
        else:
            print("  FAIL: 빈 응답 (KRX anti-bot/지역 차단 추정)")
    except Exception as e:
        print(f"  FAIL: {type(e).__name__} — KRX 직접 접근 불가(차단 추정): {str(e)[:80]}")


if __name__ == "__main__":
    print("키 설정 상태:", config.status())
    test_fnspace()
    test_finnhub()
    test_krx()
    print("\n완료. 위 PASS/FAIL 로 fnspace 구독범위와 finnhub 한국주식 커버리지를 확인하세요.")
