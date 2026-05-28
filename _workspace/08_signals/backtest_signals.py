#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8단계 시그널 전략 백테스트 (walk-forward).

매주 금요일 종가 기준 시그널 산출 → 다음 영업일 시가 가상 매매 → 가상 포트폴리오 운용.
시그널 전략 vs KOSPI buy-and-hold 비교.

전제:
  · 현재 portfolio.json 종목 풀로 시작 (실제로는 매주 portfolio도 변하지만 단순화)
  · 균등 비중 또는 portfolio.json의 weight_pct
  · 진입: 4조건 모두 충족 시 50% / -5% 시 25% / -10% 시 25%
  · 청산: MA60 하향 100% / 트레일링 -15% 100%
  · 수수료·슬리피지: 0.3% (왕복)
"""
import os, sys, json, math, statistics, datetime, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
PORTFOLIO = os.path.join(WS, "07_portfolio", "portfolio.json")
OUT_JSON = os.path.join(HERE, "backtest_results.json")
OUT_MD = os.path.join(HERE, "backtest_report.md")

WEEKS = 52  # 1년
TRADING_DAYS = 252 + 130  # 백테스트 1년 + 룩백 130일

FEE_BPS = 30  # 왕복 0.3%
INITIAL_CAPITAL = 100_000_000  # 1억원


def compute_ma(closes, n):
    if len(closes) < n: return None
    win = [c for c in closes[-n:] if c is not None]
    return sum(win) / len(win) if len(win) >= n * 0.8 else None


def compute_return(closes, days_back):
    if len(closes) < days_back + 1: return None
    a, b = closes[-days_back - 1], closes[-1]
    if not (a and b and a > 0): return None
    return (b / a - 1.0) * 100.0


def compute_vol(closes, window=60):
    if len(closes) < window + 1: return None
    rets = []
    for i in range(len(closes) - window, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and b and a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < window * 0.8: return None
    return statistics.pstdev(rets) * math.sqrt(252) * 100


def signal_at(t_idx, all_closes, kospi_closes, state):
    """t_idx 시점 (각 종목 시계열의 인덱스)에 시그널 산출.
    반환: {action: 'buy_stage1/2/3' or 'sell' or None, reason}"""
    closes = all_closes[:t_idx + 1]
    ind_close = closes[-1] if closes else None
    ma60 = compute_ma(closes, 60)
    if not (ind_close and ma60):
        return {"action": None, "reason": "데이터 부족"}

    # 청산 우선
    if state.get("stage_1_price"):
        # 추세 이탈
        if ind_close < ma60:
            return {"action": "sell", "reason": f"종가 {ind_close:.0f} < MA60 {ma60:.0f}"}
        # 트레일링 손절
        peak = state.get("peak", state["stage_1_price"])
        if ind_close > peak: peak = ind_close
        state["peak"] = peak
        if ind_close < peak * 0.85:
            return {"action": "sell", "reason": f"종가 {ind_close:.0f} < peak {peak:.0f} × 0.85"}

    # 진입 (1차 또는 분할)
    completed = set(state.get("completed_stages", []))
    if not state.get("stage_1_price"):
        # 1차 진입 조건
        ret_6m = compute_return(closes, 126)
        kospi_ret_6m = compute_return(kospi_closes[:t_idx + 1], 126) if kospi_closes else None
        sigma = compute_vol(closes, 60)
        if not (ret_6m and kospi_ret_6m and sigma):
            return {"action": None, "reason": "지표 부족"}
        # 상대 변동성 임계는 외부에서 전달 (vol_threshold 인자 추가 필요)
        # 여기선 단순화: σ < 80% (대기 데이터로 KOSPI 강세장 대응)
        if ind_close > ma60 and ret_6m > 0 and ret_6m > kospi_ret_6m and sigma < state.get("vol_threshold", 80):
            return {"action": "buy_stage1", "reason": f"4조건 충족 (close>MA60 6M={ret_6m:.1f}>KOSPI={kospi_ret_6m:.1f} σ={sigma:.1f})",
                    "price": ind_close}
    else:
        # 분할 매수 (2·3차)
        entry = state["stage_1_price"]
        dd = (ind_close / entry - 1.0) * 100
        if 2 not in completed and dd <= -5:
            return {"action": "buy_stage2", "reason": f"진입가 대비 {dd:+.1f}% (≤-5%)", "price": ind_close, "dd": dd}
        if 3 not in completed and dd <= -10:
            return {"action": "buy_stage3", "reason": f"진입가 대비 {dd:+.1f}% (≤-10%)", "price": ind_close, "dd": dd}
    return {"action": None, "reason": "조건 미충족"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=WEEKS, help="백테스트 기간(주)")
    ap.add_argument("--tickers", type=int, default=20, help="상위 N종목만 사용")
    ap.add_argument("--vol-threshold", type=float, default=None,
                    help="진입 변동성 임계(%, 미지정 시 universe σ median × 1.5)")
    args = ap.parse_args()

    print("[1/4] 입력 로드…")
    portfolio = json.load(open(PORTFOLIO, encoding="utf-8"))
    holdings = portfolio["holdings"][:args.tickers]  # 상위 N (매력도순)
    tickers = [h["ticker"] for h in holdings]
    weights = {h["ticker"]: h["weight_pct"] for h in holdings}
    w_sum = sum(weights.values())
    weights = {t: w / w_sum * 100 for t, w in weights.items()}  # 100%로 정규화
    print(f"  포트폴리오 상위 {len(tickers)}종목 사용 (비중 정규화)")

    print(f"\n[2/4] KRX {TRADING_DAYS}영업일 시계열 수집…")
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
    # KOSPI weekly (백테스트 위한 daily 시리즈 보다 weekly로 단순화)
    kospi_hist = krx.kospi_index_history(asof, weeks=TRADING_DAYS // 5 + 2)
    kospi_dates = [k["date"] for k in kospi_hist]
    kospi_closes_all = [k["close"] for k in kospi_hist]
    # 각 trading day의 KOSPI 종가에 가장 가까운 weekly 매핑 (단순화: 같은 인덱스 또는 직전 weekly)
    kospi_closes = []
    j = 0
    for ds in dates:
        # 가장 최근 kospi_dates[j] <= ds 찾기
        while j + 1 < len(kospi_dates) and kospi_dates[j + 1] <= ds:
            j += 1
        kospi_closes.append(kospi_closes_all[j] if j < len(kospi_closes_all) else None)
    print(f"  asof={asof} / {len(dates)}영업일 / KOSPI 시계열 정렬 완료")

    # 상대 변동성 임계 (universe σ median 기반)
    full_sigmas = []
    for t in tickers:
        closes = [c for c in series[t][-130:] if c is not None]
        sig = compute_vol(closes, 60)
        if sig:
            full_sigmas.append(sig)
    sigma_median = statistics.median(full_sigmas) if full_sigmas else 30
    vol_threshold = args.vol_threshold or sigma_median * 1.5
    print(f"  universe σ median={sigma_median:.1f}% → 상대 변동성 임계 {vol_threshold:.1f}%")

    print(f"\n[3/4] {args.weeks}주 walk-forward 시뮬레이션…")
    # 백테스트 시작 인덱스 (룩백 130일 후부터)
    start_idx = 130
    end_idx = min(len(dates) - 1, start_idx + args.weeks * 5)
    state = {t: {"vol_threshold": vol_threshold} for t in tickers}

    # 자본 trajectory
    capital = INITIAL_CAPITAL
    cash = INITIAL_CAPITAL
    holdings_shares = {t: 0 for t in tickers}  # 보유 주식 수
    holdings_value = {t: 0 for t in tickers}
    trades = []
    daily_value = []  # [(date, total_value)]

    # 매주 금요일 (=매 5영업일)마다 시그널 산출 + 다음 거래일 매매
    rebalance_idx = list(range(start_idx, end_idx, 5))

    for i, t_idx in enumerate(rebalance_idx):
        date = dates[t_idx]
        # 시그널 산출
        signals_now = {}
        for ticker in tickers:
            sig = signal_at(t_idx, series[ticker], kospi_closes, state[ticker])
            if sig["action"]:
                signals_now[ticker] = sig

        # 매매 실행 (다음 거래일 시가 = t_idx + 1 종가 proxy로 단순화)
        exec_idx = min(t_idx + 1, len(dates) - 1)
        for ticker, sig in signals_now.items():
            exec_price = series[ticker][exec_idx]
            if not exec_price:
                continue
            if sig["action"] == "sell":
                shares = holdings_shares[ticker]
                if shares > 0:
                    proceeds = shares * exec_price * (1 - FEE_BPS / 10000)
                    cash += proceeds
                    trades.append({"date": dates[exec_idx], "ticker": ticker, "action": "sell",
                                   "shares": shares, "price": exec_price, "proceeds": proceeds, "reason": sig["reason"]})
                    holdings_shares[ticker] = 0
                    state[ticker] = {"vol_threshold": vol_threshold}  # 상태 리셋
            elif sig["action"].startswith("buy_stage"):
                stage = int(sig["action"].split("_stage")[1])
                ratio = 0.5 if stage == 1 else 0.25
                target_value = capital * weights[ticker] / 100 * ratio
                # 사용 가능 현금 안에서
                spend = min(target_value, cash * 0.95)
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
                               "shares": shares_to_buy, "price": exec_price, "cost": cost, "reason": sig["reason"]})

        # 일별 평가액 (대략, t_idx 시점)
        port_value = cash
        for ticker in tickers:
            close = series[ticker][t_idx]
            if close and holdings_shares[ticker] > 0:
                port_value += holdings_shares[ticker] * close
        capital = port_value
        daily_value.append({"date": date, "value": port_value, "cash": cash,
                             "n_holdings": sum(1 for t in tickers if holdings_shares[t] > 0)})

    print(f"  완료: {len(rebalance_idx)} 리밸런스 사이클, {len(trades)} 매매")

    # 성과 계산
    initial = INITIAL_CAPITAL
    final = daily_value[-1]["value"] if daily_value else initial
    total_return = (final / initial - 1) * 100
    # KOSPI buy-and-hold
    kospi_start = kospi_closes[start_idx] if kospi_closes[start_idx] else 1
    kospi_end = kospi_closes[end_idx] if kospi_closes[end_idx] else kospi_start
    kospi_return = (kospi_end / kospi_start - 1) * 100

    # 샤프 / MDD
    values = [d["value"] for d in daily_value]
    weekly_rets = [(values[i] / values[i - 1] - 1) for i in range(1, len(values)) if values[i - 1] > 0]
    sharpe = None
    if weekly_rets and statistics.pstdev(weekly_rets) > 0:
        sharpe = (statistics.mean(weekly_rets) / statistics.pstdev(weekly_rets)) * math.sqrt(52)
    peak_v, mdd = initial, 0
    for v in values:
        if v > peak_v: peak_v = v
        dd = (v / peak_v - 1) * 100
        if dd < mdd: mdd = dd

    print(f"\n[4/4] 성과 산출:")
    print(f"  기간: {dates[start_idx]} ~ {dates[end_idx]} ({len(rebalance_idx)}주)")
    print(f"  전략 수익률: {total_return:+.2f}%")
    print(f"  KOSPI 수익률: {kospi_return:+.2f}%")
    print(f"  초과수익률: {total_return - kospi_return:+.2f}%p")
    print(f"  샤프지수 (주별): {sharpe:.2f}" if sharpe else "  샤프: N/A")
    print(f"  MDD: {mdd:.2f}%")
    print(f"  매수 {sum(1 for tr in trades if tr['action'].startswith('buy'))} / 매도 {sum(1 for tr in trades if tr['action'] == 'sell')}")
    print(f"  종목당 평균 보유기간 (주, 거래 기반): ~{len(rebalance_idx) * len(tickers) / max(1, len(trades))/2:.1f}")

    # 저장
    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "period_start": dates[start_idx], "period_end": dates[end_idx],
        "n_weeks": len(rebalance_idx),
        "initial_capital": initial, "final_value": final,
        "total_return_pct": round(total_return, 2),
        "kospi_return_pct": round(kospi_return, 2),
        "excess_return_pct": round(total_return - kospi_return, 2),
        "sharpe_weekly_ann": round(sharpe, 3) if sharpe else None,
        "mdd_pct": round(mdd, 2),
        "n_trades": len(trades),
        "n_buys": sum(1 for tr in trades if tr["action"].startswith("buy")),
        "n_sells": sum(1 for tr in trades if tr["action"] == "sell"),
        "vol_threshold_used": vol_threshold,
        "fee_bps": FEE_BPS,
        "tickers_used": tickers,
        "daily_value_trajectory": daily_value[-20:],  # 마지막 20주만
        "trades_sample": trades[-30:],
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    L = [f"# 8단계 백테스트 리포트 ({datetime.datetime.now().strftime('%Y-%m-%d')})\n",
         f"**기간:** {dates[start_idx]} ~ {dates[end_idx]} ({len(rebalance_idx)}주)",
         f"**초기 자본:** {initial:,}원 → **최종:** {final:,.0f}원",
         f"\n## 성과 요약\n",
         f"| 지표 | 값 |", "|---|---|",
         f"| 전략 수익률 | **{total_return:+.2f}%** |",
         f"| KOSPI 수익률 | {kospi_return:+.2f}% |",
         f"| 초과수익률 | **{total_return - kospi_return:+.2f}%p** |",
         f"| 샤프지수 (주별 ann) | {sharpe:.2f}" if sharpe else "| 샤프지수 | N/A |",
         f"| MDD | {mdd:.2f}% |",
         f"| 총 매매 | {len(trades)}건 (매수 {sum(1 for tr in trades if tr['action'].startswith('buy'))} / 매도 {sum(1 for tr in trades if tr['action'] == 'sell')}) |",
         f"| 사용 종목 | {len(tickers)}개 (포트폴리오 상위 매력도순) |",
         f"| 변동성 임계 | {vol_threshold:.1f}% (universe σ median ×1.5) |",
         f"| 수수료 (왕복) | {FEE_BPS}bps |",
         "\n## 최근 매매 샘플 (최근 30건)\n",
         "| 일자 | 티커 | 액션 | 가격 | 비고 |", "|---|---|---|---|---|"]
    for tr in trades[-30:]:
        L.append(f"| {tr['date']} | {tr['ticker']} | {tr['action']} | {tr['price']:.0f} | {tr['reason'][:40]} |")
    L.append("\n*면책: 백테스트는 과거 데이터 기반이며 미래 수익을 보장하지 않습니다. 슬리피지·세금·휴장 등 단순화 가정 포함.*")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n저장: {OUT_JSON}, {OUT_MD}")


if __name__ == "__main__":
    main()
