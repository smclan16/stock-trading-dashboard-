#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전체 파이프라인(1·2·7·8단계 통합) 24개월 백테스트.

가정:
  · 1단계: 적극투자형 고정 (constraints.json)
  · 2단계: FRED VIX·US10Y 2년 시계열 → 매주 W_macro·equity_pct 산출
  · 3~6단계: 현재 portfolio.json 종목 풀 고정 (테마 1~2년 큰 차이 없다는 가정)
  · 7+8단계: 매주 시그널·가상 매매, equity_pct에 맞춰 주식/현금 비중 조정

산출:
  · 12M·24M 누적 수익률 vs KOSPI buy-and-hold
  · 샤프, MDD, 진입/청산 빈도
  · W_macro·equity_pct 시계열
"""
import os, sys, json, math, statistics, datetime, argparse, requests
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
CONSTRAINTS = os.path.join(WS, "01_profile", "constraints.json")
PORTFOLIO = os.path.join(WS, "07_portfolio", "portfolio.json")
OUT_JSON = os.path.join(HERE, "backtest_full_results.json")
OUT_MD = os.path.join(HERE, "backtest_full_report.md")

# 백테스트 파라미터
TRADING_DAYS = 504 + 130   # 24개월 백테스트 + 룩백 130일
# v8 비용 모델 (키움증권 기준)
BUY_FEE_BPS = 1.5      # 매수 수수료 0.015%
SELL_FEE_BPS = 1.5     # 매도 수수료 0.015%
SELL_TAX_BPS = 18      # 매도 거래세 0.18%
SLIPPAGE_BPS = 5       # 슬리피지 0.05% (지정가)
CREDIT_INTEREST_PCT = 6.0  # 신용 이자 연 6% (적극투자형 equity>100 부분에 적용)
FEE_BPS = BUY_FEE_BPS + SLIPPAGE_BPS  # 하위호환 (매수 시 사용)
INITIAL = 100_000_000
CASH_ANNUAL_RETURN = 0.03  # 현금 부분 연 3% (단기 무위험)

# 매크로 3x3 매트릭스 (macro-analysis SKILL과 동일)
MACRO_MATRIX = {
    ("Low", "Low"): 1.0, ("Low", "Mod"): 0.8, ("Low", "High"): 0.5,
    ("Mod", "Low"): 0.7, ("Mod", "Mod"): 0.5, ("Mod", "High"): 0.3,
    ("High", "Low"): 0.3, ("High", "Mod"): 0.2, ("High", "High"): 0.0,
}


def bucket_vix(v):
    return "Low" if v < 20 else ("Mod" if v < 30 else "High")


def bucket_us10y(v):
    return "Low" if v < 3.5 else ("Mod" if v < 4.5 else "High")


def fetch_fred_weekly(series_id, weeks):
    """FRED 시계열을 weekly로 샘플링. last value carry-forward."""
    md = datasource.MacroData()
    # 일별 시계열 충분히 받아서 weekly로 샘플링
    obs = md._series(series_id, limit=weeks * 7 + 50)
    if not obs:
        return []
    obs = sorted(obs, key=lambda x: x["date"])  # 오래된→최신
    # 주 단위로 marker (월요일 등 일관 기준)
    weekly = []
    last_val = None
    last_dt = None
    for o in obs:
        dt = datetime.datetime.strptime(o["date"], "%Y-%m-%d").date()
        last_val = o["value"]
        last_dt = dt
        if last_dt.weekday() == 0:  # 월요일
            weekly.append({"date": o["date"], "value": last_val})
    # 마지막 값까지 포함되도록
    if weekly and last_val and (not weekly or weekly[-1]["date"] != obs[-1]["date"]):
        weekly.append({"date": obs[-1]["date"], "value": last_val})
    return weekly[-weeks:]


def compute_ma(closes, n):
    if len(closes) < n: return None
    win = [c for c in closes[-n:] if c is not None]
    return sum(win) / len(win) if len(win) >= n * 0.8 else None


def compute_return(closes, days_back):
    if len(closes) < days_back + 1: return None
    a, b = closes[-days_back - 1], closes[-1]
    if not (a and b and a > 0): return None
    return (b / a - 1.0) * 100


def compute_vol(closes, window=60):
    if len(closes) < window + 1: return None
    rets = []
    for i in range(len(closes) - window, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and b and a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < window * 0.8: return None
    return statistics.pstdev(rets) * math.sqrt(252) * 100


def signal_at(t_idx, closes, kospi_closes, state, vol_threshold):
    """시점별 시그널 산출."""
    cs = closes[:t_idx + 1]
    if not cs or cs[-1] is None:
        return None
    ind_close = cs[-1]
    ma60 = compute_ma(cs, 60)
    if not ma60:
        return None

    # v8 청산: MA60 (전량/잔여) + 약세장(KOSPI 6M < 0) MA20 50%
    ma20 = compute_ma(cs, 20)
    if state.get("stage_1_price"):
        exit_stage = state.get("exit_stage", 0)
        if ind_close < ma60:
            return {"action": "sell", "reason": "trend_break_ma60", "ratio": 1.0}
        # v8: 강세장 필터 — KOSPI 6M ≥ 0 시 MA20 청산 무효
        kospi_6m_now = compute_return(kospi_closes[:t_idx + 1], 126) if kospi_closes else None
        bear_market = kospi_6m_now is not None and kospi_6m_now < 0
        if exit_stage == 0 and ma20 and ind_close < ma20 and bear_market:
            return {"action": "sell", "reason": "ma20_partial_bear", "ratio": 0.5}

    # 진입 (1차)
    if not state.get("stage_1_price"):
        ret_6m = compute_return(cs, 126)
        kospi_ret = compute_return(kospi_closes[:t_idx + 1], 126) if kospi_closes else None
        sigma = compute_vol(cs, 60)
        if not (ret_6m is not None and kospi_ret is not None and sigma is not None):
            return None
        if ind_close > ma60 and ret_6m > 0 and ret_6m > kospi_ret and sigma < vol_threshold:
            return {"action": "buy_stage1", "price": ind_close,
                    "reason": f"6M={ret_6m:.1f} vs KOSPI {kospi_ret:.1f} σ={sigma:.1f}"}
    else:
        # v6 분할 추가 (exit_stage 0인 경우만): 2차 (-7%, 15%), 3차 (-12%, 10%)
        if state.get("exit_stage", 0) > 0:
            return None
        completed = set(state.get("completed_stages", []))
        entry = state["stage_1_price"]
        dd = (ind_close / entry - 1.0) * 100
        if 2 not in completed and dd <= -7:
            return {"action": "buy_stage2", "price": ind_close, "reason": f"DD {dd:.1f}%"}
        if 3 not in completed and dd <= -12:
            return {"action": "buy_stage3", "price": ind_close, "reason": f"DD {dd:.1f}%"}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=104, help="백테스트 기간(주)")
    ap.add_argument("--tickers", type=int, default=20)
    ap.add_argument("--vol-threshold", type=float, default=None)
    args = ap.parse_args()

    print(f"[1/5] 입력 로드 (적극투자형, {args.weeks}주 백테스트)…")
    constraints = json.load(open(CONSTRAINTS, encoding="utf-8"))
    portfolio = json.load(open(PORTFOLIO, encoding="utf-8"))
    e_min = constraints.get("equity_pct_min", 100)
    e_max = constraints.get("equity_pct_max", 150)
    holdings = portfolio["holdings"][:args.tickers]
    tickers = [h["ticker"] for h in holdings]
    weights = {h["ticker"]: h["weight_pct"] for h in holdings}
    w_sum = sum(weights.values())
    weights = {t: w / w_sum for t, w in weights.items()}  # 비율 (합 1.0)
    print(f"  적극투자형 equity {e_min}~{e_max}% / 종목 {len(tickers)}")

    print(f"\n[2/5] FRED 매크로 시계열 ({args.weeks + 4}주, weekly)…")
    vix_weekly = fetch_fred_weekly("VIXCLS", args.weeks + 4)
    us10y_weekly = fetch_fred_weekly("DGS10", args.weeks + 4)
    print(f"  VIX {len(vix_weekly)}주 / US10Y {len(us10y_weekly)}주")
    # weekly 매크로 dict (날짜 → (vix, us10y))
    vix_map = {x["date"]: x["value"] for x in vix_weekly}
    us10y_map = {x["date"]: x["value"] for x in us10y_weekly}
    macro_dates = sorted(set(vix_map.keys()) & set(us10y_map.keys()))
    macro_dates = macro_dates[-args.weeks - 1:]
    print(f"  공통 주 {len(macro_dates)}")

    print(f"\n[3/5] KRX {TRADING_DAYS}영업일 시계열…")
    krx = datasource.KRXMarket()
    asof = krx.latest_trading_date()
    dates = krx.trading_dates(asof, TRADING_DAYS)
    dates.sort()
    series = {t: [] for t in tickers}
    for ds in dates:
        daily = krx.daily(ds)
        for t in tickers:
            rec = daily.get(t)
            series[t].append(rec["close"] if rec and rec.get("close") else None)
    print(f"  asof={asof} / {len(dates)}영업일 / 종목 {len(tickers)}")

    # KOSPI weekly
    kospi_hist = krx.kospi_index_history(asof, weeks=(TRADING_DAYS // 5) + 5)
    kospi_dates_list = [k["date"] for k in kospi_hist]
    kospi_vals = [k["close"] for k in kospi_hist]
    kospi_closes = []
    j = 0
    for ds in dates:
        while j + 1 < len(kospi_dates_list) and kospi_dates_list[j + 1] <= ds:
            j += 1
        kospi_closes.append(kospi_vals[j] if j < len(kospi_vals) else None)

    # 상대 변동성 임계
    sigs_init = []
    for t in tickers:
        c = [x for x in series[t][:130] if x is not None]
        s = compute_vol(c, 60)
        if s: sigs_init.append(s)
    sigma_median = statistics.median(sigs_init) if sigs_init else 30
    vol_threshold = args.vol_threshold or sigma_median * 2.5  # v6 완화 (×1.5 → ×2.5)
    print(f"  σ median={sigma_median:.1f}% → 변동성 임계 {vol_threshold:.1f}%")

    print(f"\n[4/5] {args.weeks}주 walk-forward 시뮬레이션 (매크로+시그널 통합)…")
    start_idx = 130
    end_idx = min(len(dates) - 1, start_idx + args.weeks * 5)
    rebalance_idx = list(range(start_idx, end_idx, 5))

    state = {t: {} for t in tickers}
    holdings_shares = {t: 0 for t in tickers}
    cash = INITIAL
    weekly_log = []
    trades = []

    for i, t_idx in enumerate(rebalance_idx):
        date = dates[t_idx]
        # 매크로 (가장 최근 weekly date ≤ current date)
        applicable_macro = [d for d in macro_dates if d <= date]
        if applicable_macro:
            md = applicable_macro[-1]
            vix = vix_map.get(md)
            us10y = us10y_map.get(md)
            if vix and us10y:
                bv, bu = bucket_vix(vix), bucket_us10y(us10y)
                w_macro = MACRO_MATRIX.get((bv, bu), 0.5)
                equity_target_pct = e_min + (e_max - e_min) * w_macro
            else:
                w_macro = 0.5; equity_target_pct = (e_min + e_max) / 2
                vix = us10y = bv = bu = None
        else:
            w_macro = 0.5; equity_target_pct = (e_min + e_max) / 2
            vix = us10y = bv = bu = None

        # 시그널 생성 + 매매
        exec_idx = min(t_idx + 1, len(dates) - 1)
        for ticker in tickers:
            sig = signal_at(t_idx, series[ticker], kospi_closes, state[ticker], vol_threshold)
            if not sig:
                continue
            exec_price = series[ticker][exec_idx]
            if not exec_price:
                continue

            if sig["action"] == "sell":
                shares = holdings_shares[ticker]
                ratio = sig.get("ratio", 1.0)
                shares_to_sell = int(shares * ratio) if ratio < 1.0 else shares
                if shares_to_sell > 0:
                    # v8: 매도 비용 = 수수료 0.015% + 거래세 0.18% + 슬리피지 0.05% = 0.245%
                    sell_cost_bps = SELL_FEE_BPS + SELL_TAX_BPS + SLIPPAGE_BPS
                    proceeds = shares_to_sell * exec_price * (1 - sell_cost_bps / 10000)
                    cash += proceeds
                    trades.append({"date": dates[exec_idx], "ticker": ticker, "action": "sell",
                                    "shares": shares_to_sell, "price": exec_price, "reason": sig["reason"]})
                    holdings_shares[ticker] -= shares_to_sell
                    if holdings_shares[ticker] <= 0:
                        holdings_shares[ticker] = 0
                        state[ticker] = {}
                    elif sig["reason"] == "ma20_partial":
                        state[ticker]["exit_stage"] = 1  # 잔여 보유, 추가 매수 차단
            elif sig["action"].startswith("buy_stage"):
                stage = int(sig["action"].split("_stage")[1])
                # v6 분할 비중: 1차 75%, 2차 15%, 3차 10% (Buy & Hold 지향)
                ratio = {1: 0.75, 2: 0.15, 3: 0.10}.get(stage, 0)
                # 자본 평가
                port_v = cash + sum(holdings_shares[tk] * (series[tk][t_idx] or 0) for tk in tickers)
                # 목표 주식 비중 (equity_target_pct / 100)
                target_stock_value = port_v * (equity_target_pct / 100)
                # 종목별 한도 = target_stock × weights[ticker] × ratio
                target_for_t = target_stock_value * weights[ticker] * ratio
                spend = min(target_for_t, cash * 0.95)
                if spend <= 100: continue
                shares_to_buy = int(spend // exec_price)
                if shares_to_buy <= 0: continue
                cost = shares_to_buy * exec_price * (1 + FEE_BPS / 10000)
                if cost > cash: continue
                cash -= cost
                holdings_shares[ticker] += shares_to_buy
                state[ticker].setdefault("completed_stages", [])
                if stage == 1:
                    state[ticker]["stage_1_price"] = exec_price
                    state[ticker]["completed_stages"] = [1]
                    state[ticker]["peak"] = exec_price
                else:
                    if stage not in state[ticker]["completed_stages"]:
                        state[ticker]["completed_stages"].append(stage)
                trades.append({"date": dates[exec_idx], "ticker": ticker, "action": sig["action"],
                                "shares": shares_to_buy, "price": exec_price})

        # 평가
        stock_value = sum(holdings_shares[tk] * (series[tk][t_idx] or 0) for tk in tickers)
        cash *= (1 + CASH_ANNUAL_RETURN / 52)
        # v8: 신용 이자 — equity > 100% 사용분 (cash가 음수 = 신용 활용)
        credit_interest = 0
        if cash < 0:
            margin = -cash
            credit_interest = margin * CREDIT_INTEREST_PCT / 100 / 52  # 주별
            cash -= credit_interest
        port_value = cash + stock_value
        actual_equity_pct = (stock_value / port_value * 100) if port_value > 0 else 0
        weekly_log.append({
            "date": date, "vix": vix, "us10y": us10y,
            "vix_bucket": bv, "us10y_bucket": bu, "w_macro": w_macro,
            "equity_target_pct": round(equity_target_pct, 2),
            "actual_equity_pct": round(actual_equity_pct, 2),
            "cash": round(cash, 0), "stock_value": round(stock_value, 0),
            "port_value": round(port_value, 0),
            "credit_interest_wk": round(credit_interest, 0),
            "n_holdings": sum(1 for tk in tickers if holdings_shares[tk] > 0),
        })

    print(f"  완료: {len(rebalance_idx)} 주간 사이클, {len(trades)} 매매")

    # 12M·24M 누적 수익률
    def period_return(weeks):
        if weeks > len(weekly_log): return None
        start_v = weekly_log[-weeks]["port_value"] if weeks < len(weekly_log) else INITIAL
        end_v = weekly_log[-1]["port_value"]
        return (end_v / start_v - 1) * 100 if start_v > 0 else 0

    total_24m_return = (weekly_log[-1]["port_value"] / INITIAL - 1) * 100 if weekly_log else 0
    # 12M (52주 전 → 현재)
    if len(weekly_log) > 52:
        v_12m_ago = weekly_log[-52]["port_value"]
        ret_12m = (weekly_log[-1]["port_value"] / v_12m_ago - 1) * 100 if v_12m_ago > 0 else 0
    else:
        ret_12m = total_24m_return

    # KOSPI 12M·24M
    if kospi_closes[start_idx] and kospi_closes[end_idx]:
        kospi_24m = (kospi_closes[end_idx] / kospi_closes[start_idx] - 1) * 100
        # 12M
        idx_12m_ago = rebalance_idx[-52] if len(rebalance_idx) > 52 else rebalance_idx[0]
        kospi_12m = (kospi_closes[end_idx] / kospi_closes[idx_12m_ago] - 1) * 100 if kospi_closes[idx_12m_ago] else 0
    else:
        kospi_24m = kospi_12m = 0

    # 샤프·MDD
    values = [w["port_value"] for w in weekly_log]
    weekly_rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values)) if values[i - 1] > 0]
    sharpe = None
    if weekly_rets and statistics.pstdev(weekly_rets) > 0:
        sharpe = statistics.mean(weekly_rets) / statistics.pstdev(weekly_rets) * math.sqrt(52)
    peak_v, mdd = INITIAL, 0
    for v in values:
        if v > peak_v: peak_v = v
        dd = (v / peak_v - 1) * 100
        if dd < mdd: mdd = dd

    print(f"\n[5/5] 결과 요약")
    print(f"  기간: {dates[start_idx]} ~ {dates[end_idx]}")
    print(f"  24M 누적 수익률: {total_24m_return:+.2f}%  vs KOSPI {kospi_24m:+.2f}%  (초과 {total_24m_return - kospi_24m:+.2f}%p)")
    print(f"  12M 누적 수익률: {ret_12m:+.2f}%  vs KOSPI {kospi_12m:+.2f}%  (초과 {ret_12m - kospi_12m:+.2f}%p)")
    print(f"  샤프(주별 ann): {sharpe:.2f}" if sharpe else "  샤프: N/A")
    print(f"  MDD: {mdd:.2f}%")
    print(f"  매수 {sum(1 for tr in trades if tr['action'].startswith('buy'))} / 매도 {sum(1 for tr in trades if tr['action'] == 'sell')}")
    # 매크로 분포
    macro_regime_count = {}
    for w in weekly_log:
        if w["vix_bucket"] and w["us10y_bucket"]:
            key = f"VIX={w['vix_bucket']},US10Y={w['us10y_bucket']}"
            macro_regime_count[key] = macro_regime_count.get(key, 0) + 1
    print(f"\n  매크로 레짐 분포:")
    for k, v in sorted(macro_regime_count.items(), key=lambda x: -x[1])[:5]:
        print(f"    {k}: {v}주 ({v/len(weekly_log)*100:.1f}%)")

    # 저장
    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "period_start": dates[start_idx], "period_end": dates[end_idx],
        "n_weeks": len(rebalance_idx),
        "initial_capital": INITIAL,
        "final_value": values[-1] if values else INITIAL,
        "return_24m_pct": round(total_24m_return, 2),
        "return_12m_pct": round(ret_12m, 2),
        "kospi_24m_pct": round(kospi_24m, 2),
        "kospi_12m_pct": round(kospi_12m, 2),
        "excess_24m_pct": round(total_24m_return - kospi_24m, 2),
        "excess_12m_pct": round(ret_12m - kospi_12m, 2),
        "sharpe_weekly_ann": round(sharpe, 3) if sharpe else None,
        "mdd_pct": round(mdd, 2),
        "n_trades": len(trades),
        "vol_threshold_used": vol_threshold,
        "macro_regime_distribution": macro_regime_count,
        "weekly_log_sample": weekly_log[::4][:30],  # 매월 1회 샘플링
        "trades_sample": trades[-30:],
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    L = [f"# 전체 파이프라인 백테스트 (1·2·7·8단계 통합, {datetime.datetime.now().strftime('%Y-%m-%d')})\n",
         f"**기간:** {dates[start_idx]} ~ {dates[end_idx]} ({len(rebalance_idx)}주)",
         f"**투자자 유형:** 적극투자형 (equity {e_min}~{e_max}%) | **종목:** 상위 {len(tickers)}\n",
         "## 성과 요약\n",
         f"| 기간 | 전략 | KOSPI | 초과 |", "|---|---|---|---|",
         f"| **24M** | **{total_24m_return:+.2f}%** | {kospi_24m:+.2f}% | **{total_24m_return - kospi_24m:+.2f}%p** |",
         f"| **12M** | **{ret_12m:+.2f}%** | {kospi_12m:+.2f}% | **{ret_12m - kospi_12m:+.2f}%p** |",
         f"\n| 지표 | 값 |", "|---|---|",
         f"| 샤프지수 (주별 ann) | {sharpe:.2f}" if sharpe else "| 샤프 | N/A |",
         f"| MDD | {mdd:.2f}% |",
         f"| 매매 횟수 | {len(trades)} (매수 {sum(1 for tr in trades if tr['action'].startswith('buy'))} / 매도 {sum(1 for tr in trades if tr['action'] == 'sell')}) |",
         f"| 변동성 임계 | {vol_threshold:.1f}% |",
         "\n## 매크로 레짐 분포 (W_macro 시계열)\n",
         f"| 레짐 | 주 수 | 비율 |", "|---|---|---|"]
    for k, v in sorted(macro_regime_count.items(), key=lambda x: -x[1])[:9]:
        L.append(f"| {k} | {v} | {v/len(weekly_log)*100:.1f}% |")
    L.append("\n*면책: 백테스트는 가정·단순화 포함. 3~6단계는 현재 산출물 고정. 실제 운영 시 시점별 컨센서스 변동·신호 변동 반영 필요.*")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n저장: {OUT_JSON}, {OUT_MD}")


if __name__ == "__main__":
    main()
