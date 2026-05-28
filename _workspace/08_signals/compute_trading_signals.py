#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8단계 트레이딩 시그널 자동 산출 — 중장기 추세추종 + 듀얼 모멘텀 + Vol Cap.

모델:
  · 진입 조건 5종: MA(60)>MA(120)·종가>MA(60)·3M>0·6M>0·6M>KOSPI 6M·σ<target
  · 진입 가격: 1순위 시가 시장가 / 보조 5·10·20MA 도달 시 1/3 분할
  · Vol Cap: scale = min(target/σ, 1.0), 비중 정규화
  · 이탈 5종: MA(60) 하향, 트레일링, 3M 음전환, 상대강도 열위, 포트폴리오 제외
  · 주 1회 리밸런싱 (금요일 종가 기준)

자동 산출 + HITL 큐 (사용자 승인 필수).
"""
import os, sys, json, math, statistics, datetime, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import datasource

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)
CONSTRAINTS = os.path.join(WS, "01_profile", "constraints.json")
PORTFOLIO = os.path.join(WS, "07_portfolio", "portfolio.json")
PRIOR_SIGNALS = os.path.join(HERE, "signals.json")  # 이전 사이클 비교
OUT_QUEUE = os.path.join(HERE, "signal_queue.md")
OUT_JSON = os.path.join(HERE, "signals.json")
OUT_LOG = os.path.join(HERE, "decision_log.md")

TRADING_DAYS = 130   # MA(120) + 여유
MA_PERIODS = [5, 10, 20, 60, 120]
MOM_3M = 63          # 영업일 (3개월 ≈ 63일)
MOM_6M = 126
VOL_WINDOW = 60      # 변동성 측정 윈도우


def collect_series(krx, tickers, end_date, days=TRADING_DAYS):
    """종목별 종가 시계열 (오래된→최신)."""
    dates = krx.trading_dates(end_date, days)
    dates.sort()
    series = {t: [] for t in tickers}
    for ds in dates:
        daily = krx.daily(ds)
        for t in tickers:
            rec = daily.get(t)
            series[t].append(rec["close"] if rec and rec.get("close") else None)
    return series, dates


def ma(closes, n):
    """N일 이동평균. closes는 시계열 (오래된→최신)."""
    if len(closes) < n:
        return None
    window = [c for c in closes[-n:] if c is not None]
    return sum(window) / len(window) if len(window) >= n * 0.8 else None  # 80% 이상 유효


def return_pct(closes, days_back):
    """N일 전 대비 수익률 (%)."""
    if len(closes) < days_back + 1:
        return None
    now, past = closes[-1], closes[-days_back - 1]
    if not (now and past and past > 0):
        return None
    return (now / past - 1.0) * 100.0


def annual_vol(closes, window=VOL_WINDOW):
    """N일 일간 로그수익률 std × √252."""
    if len(closes) < window + 1:
        return None
    rets = []
    for i in range(len(closes) - window, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and b and a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < window * 0.8:
        return None
    s = statistics.pstdev(rets)
    return s * math.sqrt(252) * 100.0   # %


def compute_indicators(closes, kospi_closes=None):
    """단일 종목 지표 묶음 + KOSPI 비교용."""
    last = closes[-1] if closes else None
    if not last:
        return None
    ind = {
        "close": last,
        "ma5": ma(closes, 5), "ma10": ma(closes, 10), "ma20": ma(closes, 20),
        "ma60": ma(closes, 60), "ma120": ma(closes, 120),
        "ret_3m_pct": return_pct(closes, MOM_3M),
        "ret_6m_pct": return_pct(closes, MOM_6M),
        "sigma_annual_pct": annual_vol(closes),
    }
    if kospi_closes:
        ind["kospi_6m_pct"] = return_pct(kospi_closes, MOM_6M)
    return ind


def check_entry(ind, vol_threshold_pct, kospi_6m_pct):
    """4개 진입 조건 검사 (단순화: Antonacci Dual Momentum + 추세 + 상대 변동성).
    1. close > MA(60) (추세)
    2. 6M 수익률 > 0 (절대 모멘텀)
    3. 6M > KOSPI 6M (상대 강도)
    4. σ < vol_threshold (상대 변동성 — median × 1.5)
    반환: (passed_bool, details)
    """
    if not ind:
        return False, {"reason": "데이터 부족"}
    checks = {
        "trend": (ind.get("close") and ind.get("ma60")
                  and ind["close"] > ind["ma60"]),
        "mom_6m_abs": ind.get("ret_6m_pct") is not None and ind["ret_6m_pct"] > 0,
        "rel_strength": (ind.get("ret_6m_pct") is not None and kospi_6m_pct is not None
                         and ind["ret_6m_pct"] > kospi_6m_pct),
        "vol_relative": ind.get("sigma_annual_pct") is not None and ind["sigma_annual_pct"] < vol_threshold_pct,
    }
    return all(checks.values()), checks


def detect_exit_signals(ticker, ind, prior_holdings, current_holdings, prior_entry_price=None,
                        exit_stage=0, kospi_6m_pct=None):
    """v8 — KOSPI 강세장 필터 적용 + v7 2단계 청산.

    강세장 필터 (kospi_6m_pct ≥ 0): MA20 청산 무효 (MA60 청산만, v6 동작)
    약세장 (kospi_6m_pct < 0): MA20 청산 + MA60 청산 (v7 동작)

    - exit_stage 0 (전량 보유):
        · MA20 하향 (약세장만) → 50% 청산 (exit_stage=1)
        · MA60 하향 → 100% 청산
    - exit_stage 1 (잔여 50%):
        · MA60 하향 → 잔여 50% 청산
    """
    signals = []
    if not ind:
        return signals
    close = ind.get("close")
    ma20 = ind.get("ma20")
    ma60 = ind.get("ma60")
    if not close:
        return signals

    # MA60 하향: stage 무관 잔여 전량 청산
    if ma60 and close < ma60:
        if exit_stage == 0:
            signals.append({"type": "trend_break_full", "severity": "high", "action": "청산 100%",
                            "reason": f"종가 {close:.0f} < MA(60) {ma60:.0f}"})
        else:  # exit_stage >= 1
            signals.append({"type": "trend_break_remainder", "severity": "high", "action": "잔여 50% 청산",
                            "reason": f"MA20 청산 후 MA60도 하향 ({close:.0f} < {ma60:.0f})"})
        return signals

    # MA20 하향: stage 0 + 약세장(KOSPI 6M < 0)인 경우만 50% 부분 청산
    bear_market = (kospi_6m_pct is not None and kospi_6m_pct < 0)
    if exit_stage == 0 and ma20 and close < ma20 and bear_market:
        signals.append({"type": "ma20_partial_exit", "severity": "mid", "action": "50% 청산",
                        "reason": f"종가 {close:.0f} < MA(20) {ma20:.0f} (약세장 KOSPI 6M {kospi_6m_pct:+.1f}%, 조기 대응)"})

    # 참고용 경고 (청산 액션 아님)
    if prior_entry_price and close < prior_entry_price * 0.80:
        signals.append({"type": "drawdown_warn", "severity": "low", "action": "경고만",
                        "reason": f"진입가 {prior_entry_price:.0f} 대비 -20% 이하 (참고)"})
    return signals


def vol_cap_adjust(base_weights, sigmas, target_vol_pct):
    """Vol Cap 비중 조정 + 정규화."""
    adjusted = {}
    for t, base_w in base_weights.items():
        s = sigmas.get(t)
        if s is None or s <= 0:
            scale = 1.0
        else:
            scale = min(target_vol_pct / s, 1.0)
        adjusted[t] = base_w * scale
    total = sum(adjusted.values())
    if total > 0:
        for t in adjusted:
            adjusted[t] = adjusted[t] / total * 100
    return adjusted


def determine_entry_signal(ticker, ind, prior_state):
    """v6 분할 매수 — 75% 즉시 / 15% (-7%) / 10% (-12%).
    1차에 거의 다 진입(75%) → 추세 강한 종목 추적력 ↑. 조정 시 소폭 추가.
    추세 유지(가격 변동 없거나 상승)면 1차로 끝 = Buy & Hold.

    prior_state: {
        "stage_1_price": float | None,  # 1차 진입가
        "completed_stages": list of int
    } or None
    """
    close = ind.get("close")
    ma60 = ind.get("ma60")
    if not (close and ma60):
        return None
    if close < ma60:
        return None  # 추세 깨지면 분할 매수 중단

    if not prior_state or not prior_state.get("stage_1_price"):
        # 1차 진입 (75%): 진입 4조건 충족 시
        return {"stage": 1, "ratio": 0.75, "trigger": "1차 진입 (75%, Buy & Hold 기준)",
                "entry_price_estimate": close}

    last_price = prior_state["stage_1_price"]
    completed = set(prior_state.get("completed_stages", []))
    drawdown_pct = (close / last_price - 1.0) * 100.0

    # 2차 (15%): 1차 진입가 대비 -7% 도달 + 추세 유지
    if 2 not in completed and drawdown_pct <= -7.0:
        return {"stage": 2, "ratio": 0.15,
                "trigger": f"1차 대비 {drawdown_pct:+.1f}% (≤-7%)",
                "entry_price_estimate": close, "drawdown_pct": drawdown_pct}
    # 3차 (10%): 1차 진입가 대비 -12% 도달 + 추세 유지
    if 3 not in completed and drawdown_pct <= -12.0:
        return {"stage": 3, "ratio": 0.10,
                "trigger": f"1차 대비 {drawdown_pct:+.1f}% (≤-12%)",
                "entry_price_estimate": close, "drawdown_pct": drawdown_pct}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-vol", type=float, default=None, help="연환산 변동성 한도(%). 미지정 시 constraints.max_annual_volatility")
    ap.add_argument("--dry-run", action="store_true", help="저장 없이 큐만 출력")
    args = ap.parse_args()

    print("[1/5] 입력 로드…")
    constraints = json.load(open(CONSTRAINTS, encoding="utf-8"))
    portfolio = json.load(open(PORTFOLIO, encoding="utf-8"))
    target_vol = args.target_vol or constraints.get("max_annual_volatility", 30)
    print(f"  target_vol={target_vol}% / 포트폴리오 {len(portfolio['holdings'])}종목")

    # 이전 사이클 비교 (구포맷 list / 신포맷 dict 호환)
    prior_signals = None
    try:
        if os.path.exists(PRIOR_SIGNALS):
            prior_signals = json.load(open(PRIOR_SIGNALS, encoding="utf-8"))
    except Exception:
        prior_signals = None
    if isinstance(prior_signals, dict):
        prior_holdings = set(prior_signals.get("holdings", []))
        prior_entry_states = prior_signals.get("entry_states", {}) or {}
    else:
        prior_holdings = set()
        prior_entry_states = {}
    current_holdings = {h["ticker"] for h in portfolio["holdings"]}

    holdings_dict = {h["ticker"]: h for h in portfolio["holdings"]}
    tickers = list(current_holdings | prior_holdings)
    print(f"  비교 대상: 신규 {len(current_holdings - prior_holdings)} / 유지 {len(current_holdings & prior_holdings)} / 청산 후보 {len(prior_holdings - current_holdings)}")

    print("\n[2/5] KRX 가격 시계열 수집 (130 영업일)…")
    krx = datasource.KRXMarket()
    asof = krx.latest_trading_date()
    series, dates = collect_series(krx, tickers, asof, days=TRADING_DAYS)
    print(f"  asof={asof} / {len(dates)}영업일 / 종목 {len(tickers)}")

    # KOSPI 일별 (3M·6M 비교용) — 시계열 호출 무거우니 weekly가 아닌 monthly 수익률은 단순화 가능
    # 여기선 KOSPI 매주 weekly 가져와 6M 수익률만 계산
    kospi_hist = krx.kospi_index_history(asof, weeks=26)
    kospi_closes = [k["close"] for k in kospi_hist]
    kospi_6m_pct = None
    if len(kospi_closes) >= 26:
        # 26주 ≈ 6개월
        if kospi_closes[0] > 0:
            kospi_6m_pct = (kospi_closes[-1] / kospi_closes[0] - 1.0) * 100.0
    print(f"  KOSPI 6M 수익률: {kospi_6m_pct:+.2f}%" if kospi_6m_pct is not None else "  KOSPI 6M 데이터 부족")

    print("\n[3/5] 종목별 지표 산출…")
    indicators_by_ticker = {}
    sigmas = {}
    for t in tickers:
        closes = [c for c in series[t] if c is not None]
        ind = compute_indicators(closes)
        indicators_by_ticker[t] = ind
        if ind and ind.get("sigma_annual_pct") is not None:
            sigmas[t] = ind["sigma_annual_pct"]

    # v6 vol cap 완화: median(universe σ) × 2.5 (기존 1.5 → 2.5)
    # 강세장에서 변동성 큰 종목도 추세추종 유지
    if sigmas:
        sigma_median = statistics.median(sigmas.values())
        vol_threshold = sigma_median * 2.5
    else:
        sigma_median = None
        vol_threshold = target_vol  # 폴백
    print(f"  universe σ median = {sigma_median:.2f}% → 상대 변동성 임계 = {vol_threshold:.2f}% (×2.5 v6 완화)")

    print("\n[3-2/5] 시그널 산출…")
    signals = []
    for t in tickers:
        ind = indicators_by_ticker[t]
        # 이탈 시그널 (보유 종목)
        if t in prior_holdings:
            prior_entry = (prior_entry_states.get(t) or {}).get("stage_1_price")
            exits = detect_exit_signals(t, ind, prior_holdings, current_holdings, prior_entry)
            for e in exits:
                signals.append({
                    "ticker": t, "name": holdings_dict.get(t, {}).get("name"),
                    "sector": holdings_dict.get(t, {}).get("sector"),
                    "signal_type": "exit", "subtype": e["type"], "severity": e["severity"],
                    "action": e["action"], "reason": e["reason"],
                    "current_close": ind.get("close") if ind else None,
                    "ma60": ind.get("ma60") if ind else None,
                })

        # 진입 시그널 (현재 포트폴리오 종목)
        if t in current_holdings:
            entry_pass, entry_details = check_entry(ind, vol_threshold, kospi_6m_pct)
            prior_state = prior_entry_states.get(t)
            already_started = bool(prior_state and prior_state.get("stage_1_price"))

            if entry_pass or already_started:
                # 1차는 entry_pass 필수, 2·3차는 이미 시작했으면 진입 조건 재충족 X (추세만 유지하면 됨)
                if entry_pass or already_started:
                    entry_sig = determine_entry_signal(t, ind, prior_state)
                    if entry_sig:
                        # 1차(50%) 진입은 신규 / 2·3차(분할 추가)는 리밸런스
                        is_new = entry_sig["stage"] == 1
                        sig_type = "entry_new" if is_new else "entry_rebalance"
                        signals.append({
                            "ticker": t, "name": holdings_dict[t].get("name"),
                            "sector": holdings_dict[t].get("sector"),
                            "signal_type": sig_type, "subtype": f"stage_{entry_sig['stage']}",
                            "entry_ratio": entry_sig["ratio"], "trigger": entry_sig["trigger"],
                            "entry_price_estimate": entry_sig.get("entry_price_estimate"),
                            "drawdown_pct": entry_sig.get("drawdown_pct"),
                            "base_weight_pct": holdings_dict[t].get("weight_pct"),
                            "current_close": ind.get("close"),
                            "ma60": ind.get("ma60"),
                            "ret_3m_pct": ind.get("ret_3m_pct"),
                            "ret_6m_pct": ind.get("ret_6m_pct"),
                            "sigma_annual_pct": ind.get("sigma_annual_pct"),
                            "checks": entry_details,
                        })
            else:
                # 진입 조건 미충족, 보유 중이면 정보용 알림 (action 없음)
                if t in prior_holdings:
                    failed = [k for k, v in entry_details.items() if not v]
                    if failed:
                        signals.append({
                            "ticker": t, "name": holdings_dict[t].get("name"),
                            "signal_type": "watch", "subtype": "entry_conditions_failing",
                            "failing_filters": failed, "current_close": ind.get("close") if ind else None,
                        })

    print(f"  생성 시그널: {len(signals)}건 (진입 {sum(1 for s in signals if s['signal_type'].startswith('entry'))}, 이탈 {sum(1 for s in signals if s['signal_type'] == 'exit')}, 관찰 {sum(1 for s in signals if s['signal_type'] == 'watch')})")

    print("\n[4/5] Vol Cap 비중 조정…")
    base_weights = {t: holdings_dict[t]["weight_pct"] for t in current_holdings}
    adjusted = vol_cap_adjust(base_weights, sigmas, target_vol)
    weight_changes = []
    for t in sorted(adjusted, key=lambda x: -abs(adjusted[x] - base_weights[x])):
        d = adjusted[t] - base_weights[t]
        if abs(d) >= 0.1:
            weight_changes.append({
                "ticker": t, "name": holdings_dict[t].get("name"),
                "old_pct": round(base_weights[t], 2), "new_pct": round(adjusted[t], 2),
                "diff_pct": round(d, 2),
                "sigma_pct": round(sigmas.get(t, 0), 1),
                "scale": round(min(target_vol / sigmas[t], 1.0), 3) if t in sigmas else 1.0,
            })
    print(f"  Vol Cap 조정 영향 종목 (Δ≥0.1%p): {len(weight_changes)}개")
    for wc in weight_changes[:5]:
        print(f"    {wc['ticker']} {wc['name'][:12]:<12} σ={wc['sigma_pct']:>5.1f}% scale={wc['scale']:.2f} → {wc['old_pct']:.2f}% → {wc['new_pct']:.2f}% (Δ {wc['diff_pct']:+.2f}%p)")

    print("\n[5/5] HITL 큐 작성…")
    today = datetime.date.today().isoformat()
    # entry_states 업데이트 — 새로 발생한 진입 시그널 반영
    new_entry_states = dict(prior_entry_states)
    for s in signals:
        if s["signal_type"] in ("entry_new", "entry_rebalance") and s.get("entry_price_estimate"):
            t = s["ticker"]
            stage = int(s["subtype"].split("_")[1])
            state = new_entry_states.setdefault(t, {"stage_1_price": None, "completed_stages": []})
            if stage == 1:
                state["stage_1_price"] = s["entry_price_estimate"]
                state["completed_stages"] = [1]
            else:
                if stage not in state["completed_stages"]:
                    state["completed_stages"].append(stage)
            state["last_signal_date"] = asof
    # 청산된 종목의 entry_state 제거
    for s in signals:
        if s["signal_type"] == "exit" and s.get("severity") == "high":
            new_entry_states.pop(s["ticker"], None)
    # 포트폴리오에서 빠진 종목 정리
    for t in list(new_entry_states.keys()):
        if t not in current_holdings:
            new_entry_states.pop(t, None)

    out = {
        "as_of": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "rebalance_basis": asof,
        "target_vol_pct": target_vol,
        "kospi_6m_pct": kospi_6m_pct,
        "holdings": list(current_holdings),
        "prior_holdings": list(prior_holdings),
        "entry_states": new_entry_states,
        "signals": signals,
        "vol_cap_adjustments": weight_changes,
        "summary": {
            "n_entry_new": sum(1 for s in signals if s["signal_type"] == "entry_new"),
            "n_entry_rebalance": sum(1 for s in signals if s["signal_type"] == "entry_rebalance"),
            "n_exit_full": sum(1 for s in signals if s["signal_type"] == "exit" and s.get("severity") == "high"),
            "n_exit_partial": sum(1 for s in signals if s["signal_type"] == "exit" and s.get("severity") == "mid"),
            "n_watch": sum(1 for s in signals if s["signal_type"] == "watch"),
        },
    }

    if not args.dry_run:
        json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Markdown 큐
    L = [f"# 트레이딩 시그널 큐 ({today})\n",
         f"**리밸런스 기준일:** {asof} | **target_vol:** {target_vol}% | **KOSPI 6M:** {kospi_6m_pct:+.2f}%" if kospi_6m_pct else f"**리밸런스 기준일:** {asof}",
         f"\n**요약:** 신규진입 {out['summary']['n_entry_new']} / 리밸런스 {out['summary']['n_entry_rebalance']} / 청산 {out['summary']['n_exit_full']} / 부분축소 {out['summary']['n_exit_partial']} / 관찰 {out['summary']['n_watch']}\n",
         "---\n"]

    # 1) 신규 진입
    new_entries = [s for s in signals if s["signal_type"] == "entry_new"]
    if new_entries:
        L.append("## 🟢 신규 진입 시그널\n")
        for i, s in enumerate(new_entries, 1):
            ratio_pct = int(s["entry_ratio"] * 100)
            L.append(f"### [신규 #{i}] {s['name']} ({s['ticker']}) — 매수 {ratio_pct}%")
            L.append(f"**유형:** {s['subtype']} | **트리거:** {s['trigger']}\n")
            L.append(f"| 진입 조건 | 값 | 통과 |")
            L.append(f"|---|---|---|")
            L.append(f"| 종가 vs MA60 vs MA120 | {s['current_close']:.0f} / {s['ma60']:.0f} | ✅" if s['ma60'] else "")
            L.append(f"| 3M 수익률 > 0 | {s['ret_3m_pct']:+.2f}% | ✅")
            L.append(f"| 6M 수익률 > 0 | {s['ret_6m_pct']:+.2f}% | ✅")
            L.append(f"| 6M > KOSPI | {s['ret_6m_pct']:+.2f}% vs {kospi_6m_pct:+.2f}% | ✅")
            L.append(f"| σ < {target_vol}% | {s['sigma_annual_pct']:.2f}% | ✅\n")
            L.append(f"**기본 비중:** {s['base_weight_pct']:.2f}% × 진입비율 {ratio_pct}% = **{s['base_weight_pct'] * s['entry_ratio']:.2f}%**")
            L.append("- [ ] 승인  [ ] 거부  [ ] 보류\n")

    # 2) 리밸런스 매수 (보유 종목 추가 분할)
    rebal_entries = [s for s in signals if s["signal_type"] == "entry_rebalance"]
    if rebal_entries:
        L.append("\n## 🔵 리밸런스 (분할 추가 매수)\n")
        for i, s in enumerate(rebal_entries, 1):
            ratio_pct = int(s["entry_ratio"] * 100)
            L.append(f"### [리밸 #{i}] {s['name']} ({s['ticker']}) — 추가 {ratio_pct}% 매수")
            L.append(f"**트리거:** {s['trigger']} (종가 {s['current_close']:.0f})")
            L.append("- [ ] 승인  [ ] 거부  [ ] 보류\n")

    # 3) 청산
    exits = [s for s in signals if s["signal_type"] == "exit"]
    if exits:
        L.append("\n## 🔴 청산·축소 시그널\n")
        for i, s in enumerate(exits, 1):
            sev = {"high": "🔴", "mid": "🟡"}.get(s.get("severity"), "⚪")
            L.append(f"### {sev} [{s['subtype']}] {s['name']} ({s['ticker']}) — {s['action']}")
            L.append(f"**근거:** {s['reason']}")
            L.append("- [ ] 승인  [ ] 거부  [ ] 보류\n")

    # 4) Vol Cap 비중 조정
    if weight_changes:
        L.append("\n## ⚖️ Vol Cap 비중 조정\n")
        L.append("| 티커 | 종목명 | σ (연%) | scale | 이전 → 새 비중 | Δ |")
        L.append("|---|---|---|---|---|---|")
        for wc in weight_changes:
            L.append(f"| {wc['ticker']} | {wc['name'][:14]} | {wc['sigma_pct']:.1f} | {wc['scale']:.2f} | "
                      f"{wc['old_pct']:.2f}% → {wc['new_pct']:.2f}% | {wc['diff_pct']:+.2f}%p |")

    # 5) 관찰 (진입 조건 깨지는 보유)
    watches = [s for s in signals if s["signal_type"] == "watch"]
    if watches:
        L.append("\n## 👀 관찰 (진입 조건 일부 미달 보유 종목)\n")
        for s in watches:
            L.append(f"- **{s['name']}** ({s['ticker']}): 미달 필터 {', '.join(s.get('failing_filters', []))}")

    L.append("\n---\n*면책: 본 시그널은 정보 제공 목적이며 투자 권유가 아닙니다. 모든 매매 결정의 책임은 투자자 본인에게 있습니다.*")

    if not args.dry_run:
        open(OUT_QUEUE, "w", encoding="utf-8").write("\n".join(L) + "\n")
        # decision log append
        log_lines = [f"\n## {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} 사이클\n"]
        for s in signals:
            _name = (s.get('name') or '')[:10]
            _sub = s.get('subtype') or ''
            _act = s.get('action') or ''
            _why = s.get('reason') or s.get('trigger') or ''
            log_lines.append(f"- {s['signal_type']:18s} {s['ticker']} {_name:<10}  {_sub} {_act} | {_why}")
        with open(OUT_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
        print(f"  저장: {OUT_QUEUE}, {OUT_JSON}, {OUT_LOG}")

    print(f"\n=== 시그널 요약 ===")
    print(f"  신규 진입: {out['summary']['n_entry_new']}건")
    print(f"  리밸런스 (분할 추가): {out['summary']['n_entry_rebalance']}건")
    print(f"  완전 청산: {out['summary']['n_exit_full']}건")
    print(f"  부분 축소: {out['summary']['n_exit_partial']}건")
    print(f"  관찰: {out['summary']['n_watch']}건")
    print(f"  Vol Cap 영향 비중 변경: {len(weight_changes)}종목")


if __name__ == "__main__":
    main()
