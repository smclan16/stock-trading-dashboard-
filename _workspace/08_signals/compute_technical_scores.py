#!/usr/bin/env python3
"""추세추종 친화도 기술적 점수 산출기 (0~100점, 6축 매력도와 결합)

7개 지표:
  1) MA 정배열 (MA5>MA20>MA60>MA120)              20점
  2) MA60 이격 (% margin)                          15점
  3) 6M 절대모멘텀 + KOSPI 상대모멘텀              15점
  4) 거래량 트렌드 (5d vs 20d avg)                 10점
  5) 52주 위치 (40~85% = 추세추종 sweet spot)      15점
  6) RSI(14) (50~70 추세 강화 / <30·>80 감점)      10점
  7) 변동성 (universe σ 중앙값 대비)                15점

출력:
  _workspace/08_signals/technical_scores.json
  _workspace/08_signals/final_portfolio.json (Top 5~10)
  _workspace/08_signals/final_portfolio.md   (HITL 검토용)
"""
import sys, os, json, math, argparse, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from lib.datasource import KRXMarket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PF_PATH = os.path.join(ROOT, '07_portfolio', 'portfolio.json')
SIG_PATH = os.path.join(ROOT, '08_signals', 'signals.json')
OUT_TECH = os.path.join(ROOT, '08_signals', 'technical_scores.json')
OUT_FINAL = os.path.join(ROOT, '08_signals', 'final_portfolio.json')
OUT_MD = os.path.join(ROOT, '08_signals', 'final_portfolio.md')


def calc_ma(prices, n):
    if len(prices) < n:
        return None
    return sum(prices[-n:]) / n


def calc_rsi(prices, n=14):
    if len(prices) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        diff = prices[i] - prices[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_g = sum(gains) / n
    avg_l = sum(losses) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def calc_returns(prices, days):
    if len(prices) < days + 1:
        return None
    return (prices[-1] / prices[-days - 1] - 1) * 100


def score_alignment(ma5, ma20, ma60, ma120):
    """이동평균선 정배열 — 20점"""
    if None in (ma5, ma20, ma60, ma120):
        return 0
    score = 0
    if ma5 > ma20: score += 5
    if ma20 > ma60: score += 6
    if ma60 > ma120: score += 6
    if ma5 > ma20 > ma60 > ma120: score += 3  # 완전 정배열 보너스
    return score


def score_ma60_margin(close, ma60):
    """MA60 이격률 — 15점 (5~25% = sweet spot)"""
    if not close or not ma60:
        return 0
    margin = (close / ma60 - 1) * 100
    if margin < 0: return 0
    if margin <= 5: return 15 * (margin / 5)  # 0~15
    if margin <= 25: return 15
    if margin <= 40: return 15 - (margin - 25) * 0.6  # 점진 감점
    return 6  # 과열


def score_momentum(ret_6m, kospi_6m):
    """6M 절대 + 상대 모멘텀 — 15점"""
    if ret_6m is None:
        return 0
    sc = 0
    if ret_6m > 0: sc += 8
    if kospi_6m is not None and ret_6m > kospi_6m: sc += 7
    return sc


def score_volume(vol_5d_avg, vol_20d_avg):
    """거래량 트렌드 — 10점"""
    if not vol_20d_avg or vol_20d_avg <= 0:
        return 0
    ratio = vol_5d_avg / vol_20d_avg
    if ratio < 0.7: return 2     # 거래량 위축
    if ratio < 1.0: return 6
    if ratio < 1.5: return 10    # 적당한 거래량 증가
    if ratio < 2.5: return 8     # 거래량 급증 (과열 우려)
    return 5                      # 극단적 급증 (이상 신호)


def score_52w_position(close, high_52w, low_52w):
    """52주 위치 — 15점 (40~85% = 추세추종 최적)"""
    if not all([close, high_52w, low_52w]) or high_52w <= low_52w:
        return 0
    pos = (close - low_52w) / (high_52w - low_52w)  # 0~1
    if pos < 0.20: return 2      # 저점 근처 (반등 미확인)
    if pos < 0.40: return 8
    if pos <= 0.85: return 15    # sweet spot
    if pos <= 0.95: return 10    # 신고가 근처
    return 6                      # 천장 부근


def score_rsi(rsi):
    """RSI — 10점"""
    if rsi is None: return 0
    if rsi < 30: return 2        # 과매도 (추세 약함)
    if rsi < 50: return 6
    if rsi <= 70: return 10      # 추세 강화 영역
    if rsi <= 80: return 6
    return 2                      # 과매수


def score_volatility(sigma_pct, univ_median_pct):
    """변동성 — 15점 (낮을수록 유리, 단 너무 낮으면 추세 부재)"""
    if not sigma_pct or not univ_median_pct:
        return 0
    ratio = sigma_pct / univ_median_pct
    if ratio < 0.5: return 8     # 너무 낮음 (추세 약함)
    if ratio < 0.8: return 15    # 안정적
    if ratio < 1.2: return 12
    if ratio < 1.5: return 7
    return 3                      # 과변동


def compute_tech_score(prices, volumes, kospi_6m, univ_sigma_median):
    """가격 시계열 → 기술점수 (0~100) + 세부 breakdown"""
    if len(prices) < 130:
        return None
    close = prices[-1]
    ma5 = calc_ma(prices, 5)
    ma20 = calc_ma(prices, 20)
    ma60 = calc_ma(prices, 60)
    ma120 = calc_ma(prices, 120)
    ret_6m = calc_returns(prices, 120)
    rsi14 = calc_rsi(prices, 14)
    vol_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else None
    vol_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    high_52w = max(prices[-min(252, len(prices)):])
    low_52w = min(prices[-min(252, len(prices)):])
    # σ annual (130d 일별 log return std × √252)
    rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    if len(rets) >= 20:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
        sigma_annual_pct = math.sqrt(var * 252) * 100
    else:
        sigma_annual_pct = None

    sc_align = score_alignment(ma5, ma20, ma60, ma120)
    sc_margin = score_ma60_margin(close, ma60)
    sc_mom = score_momentum(ret_6m, kospi_6m)
    sc_vol = score_volume(vol_5d, vol_20d)
    sc_pos = score_52w_position(close, high_52w, low_52w)
    sc_rsi = score_rsi(rsi14)
    sc_sigma = score_volatility(sigma_annual_pct, univ_sigma_median)

    total = sc_align + sc_margin + sc_mom + sc_vol + sc_pos + sc_rsi + sc_sigma

    return {
        'tech_score': round(total, 2),
        'breakdown': {
            'alignment(20)': round(sc_align, 1),
            'ma60_margin(15)': round(sc_margin, 1),
            'momentum(15)': round(sc_mom, 1),
            'volume(10)': round(sc_vol, 1),
            'pos_52w(15)': round(sc_pos, 1),
            'rsi(10)': round(sc_rsi, 1),
            'volatility(15)': round(sc_sigma, 1),
        },
        'indicators': {
            'close': close,
            'ma5': round(ma5, 1) if ma5 else None,
            'ma20': round(ma20, 1) if ma20 else None,
            'ma60': round(ma60, 1) if ma60 else None,
            'ma120': round(ma120, 1) if ma120 else None,
            'ma60_margin_pct': round((close / ma60 - 1) * 100, 2) if ma60 else None,
            'ret_6m_pct': round(ret_6m, 2) if ret_6m is not None else None,
            'rsi14': round(rsi14, 1) if rsi14 is not None else None,
            'vol_5d_to_20d_ratio': round(vol_5d / vol_20d, 2) if (vol_5d and vol_20d) else None,
            'pos_52w_pct': round((close - low_52w) / (high_52w - low_52w) * 100, 1) if high_52w > low_52w else None,
            'sigma_annual_pct': round(sigma_annual_pct, 1) if sigma_annual_pct else None,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top-n', type=int, default=8, help='최종 종목 수 (5~10 권장)')
    ap.add_argument('--w-tech', type=float, default=0.5, help='기술점수 가중 (0~1)')
    ap.add_argument('--days', type=int, default=130, help='가격 시계열 길이 (영업일)')
    args = ap.parse_args()

    print(f'[1/5] 입력 로드…')
    pf = json.load(open(PF_PATH, encoding='utf-8'))
    sigs = json.load(open(SIG_PATH, encoding='utf-8'))
    holdings = pf['holdings']
    as_of = pf['as_of']
    print(f'  보유 후보: {len(holdings)}종목')

    # exit 종목
    exit_set = set(s['ticker'] for s in sigs['signals'] if 'exit' in s.get('signal_type', '').lower())
    print(f'  MA60 이탈 보류: {len(exit_set)}종목')

    print(f'[2/5] KRX {args.days}영업일 시계열 수집…')
    krx = KRXMarket()
    sig_asof = (sigs.get('rebalance_basis') or sigs.get('as_of') or as_of).replace('-', '')[:8]
    # 공휴일은 trading_dates에 평일로 포함되나 daily()가 빈 응답 → 손실 보전 위해 여유 요청
    _need = args.days
    dates = krx.trading_dates(sig_asof, int(_need * 1.3) + 15)
    dates.sort()  # 오래된→최신 (시계열 분석용)
    print(f'  asof={sig_asof} / {len(dates)} 영업일 (오래된={dates[0]} → 최신={dates[-1]})')

    # KOSPI 6M (≈26주)
    kospi_close_history = krx.kospi_index_history(sig_asof, 26)
    if kospi_close_history and len(kospi_close_history) >= 2:
        closes = [row['close'] for row in kospi_close_history if 'close' in row]
        kospi_6m = (closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 else None
    else:
        kospi_6m = None
    print(f'  KOSPI 6M: {kospi_6m:.2f}%' if kospi_6m is not None else '  KOSPI 6M: N/A')

    # 종목별 시계열 (KRX daily 반복)
    price_map = {h['ticker']: [] for h in holdings}
    volume_map = {h['ticker']: [] for h in holdings}
    for i, d in enumerate(dates):
        try:
            day = krx.daily(d)
        except Exception:
            continue
        for t in price_map.keys():
            if t in day and day[t].get('close'):
                price_map[t].append(day[t]['close'])
                volume_map[t].append(day[t].get('volume') or 0)
        if (i + 1) % 30 == 0:
            print(f'    {i+1}/{len(dates)}일 수집…')
    print(f'  완료 — 종목별 평균 시계열 길이: {sum(len(v) for v in price_map.values()) // max(1, len(price_map))}')

    # universe σ median (130d 기반)
    sigmas = []
    for t, ps in price_map.items():
        if len(ps) >= 60:
            rets = [math.log(ps[i] / ps[i - 1]) for i in range(1, len(ps)) if ps[i - 1] > 0]
            if len(rets) >= 20:
                mu = sum(rets) / len(rets)
                var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
                sigmas.append(math.sqrt(var * 252) * 100)
    sigmas.sort()
    univ_sigma_median = sigmas[len(sigmas) // 2] if sigmas else 50
    print(f'  universe σ median: {univ_sigma_median:.1f}%')

    print(f'[3/5] 종목별 기술점수 산출…')
    tech_results = {}
    for h in holdings:
        t = h['ticker']
        ps = price_map[t]
        vs = volume_map[t]
        if len(ps) < 130:
            tech_results[t] = {'name': h['name'], 'tech_score': None, 'note': f'시계열 부족 ({len(ps)}일)'}
            continue
        res = compute_tech_score(ps, vs, kospi_6m, univ_sigma_median)
        if res:
            res['name'] = h['name']
            res['ma60_exit_signal'] = (t in exit_set)
            tech_results[t] = res

    with open(OUT_TECH, 'w', encoding='utf-8') as f:
        json.dump({
            'as_of': sig_asof,
            'method': '7-indicator trend-following affinity (0~100)',
            'kospi_6m_pct': round(kospi_6m, 2) if kospi_6m else None,
            'universe_sigma_median_pct': round(univ_sigma_median, 1),
            'scores': tech_results,
        }, f, ensure_ascii=False, indent=2)
    print(f'  저장: {OUT_TECH}')

    print(f'[4/5] 6축 매력도 × 기술점수 결합 → Top {args.top_n} 선별…')
    w_a = 1 - args.w_tech
    w_t = args.w_tech
    combined = []
    for h in holdings:
        t = h['ticker']
        tech = tech_results.get(t, {})
        ts = tech.get('tech_score')
        att = h.get('attractiveness') or 0
        if ts is None or tech.get('ma60_exit_signal'):
            cb = None
        else:
            cb = w_a * att + w_t * ts
        combined.append({
            'ticker': t, 'name': h['name'], 'sector': h.get('sector'),
            'mcap_eok': h.get('mcap_eok'), 'size_tier': h.get('size_tier'),
            'attractiveness': att, 'tech_score': ts, 'combined': cb,
            'matched_ideas': h.get('matched_ideas'), 'is_default_pick': h.get('is_default_pick', False),
            'macro_beta': h.get('macro_beta'),
            'ma60_exit_signal': tech.get('ma60_exit_signal', False),
            'tech_breakdown': tech.get('breakdown'),
            'indicators': tech.get('indicators'),
        })
    # Top-N (combined 점수 있고 MA60 이탈 아님)
    candidates = [c for c in combined if c['combined'] is not None]
    candidates.sort(key=lambda x: -x['combined'])
    top = candidates[:args.top_n]
    # 비중 = combined 점수 정규화 (1차 단순)
    total_score = sum(c['combined'] for c in top)
    for c in top:
        c['weight_pct_raw'] = round(c['combined'] / total_score * 100, 3)

    # 시총별 cap 적용 (대형 15, 중형 10, 소형 5)
    SIZE_CAPS = {'large': 25, 'mid': 18, 'small': 12}  # 5~10종목 집중 포트라 cap 완화
    def cap_for(tier):
        return SIZE_CAPS.get(tier or 'mid', 18)
    # 반복 cap
    weights = {c['ticker']: c['weight_pct_raw'] for c in top}
    for _ in range(50):
        viol = [c for c in top if weights[c['ticker']] > cap_for(c['size_tier'])]
        if not viol:
            break
        excess = 0
        for c in viol:
            cap = cap_for(c['size_tier'])
            excess += weights[c['ticker']] - cap
            weights[c['ticker']] = cap
        # 여유 있는 종목에 점수 비례 재분배
        room = {c['ticker']: cap_for(c['size_tier']) - weights[c['ticker']] for c in top if c not in viol and weights[c['ticker']] < cap_for(c['size_tier'])}
        if not room:
            break
        score_sum = sum(c['combined'] for c in top if c['ticker'] in room)
        for c in top:
            if c['ticker'] in room and score_sum > 0:
                add = excess * c['combined'] / score_sum
                add = min(add, room[c['ticker']])
                weights[c['ticker']] += add
    # 합 100% 정규화
    total_w = sum(weights.values())
    for c in top:
        c['weight_pct'] = round(weights[c['ticker']] / total_w * 100, 3)
        c['capital_weight_pct'] = round(c['weight_pct'] * pf['equity_pct'] / 100, 3)

    final = {
        'as_of': datetime.datetime.now().isoformat(),
        'investor_type': pf['investor_type'],
        'equity_pct': pf['equity_pct'],
        'regime': pf['regime'],
        'method': f'추세추종 친화도 기술점수 × 6축 매력도 결합 (w_attr={w_a}, w_tech={w_t}), Top {args.top_n}',
        'kospi_6m_pct': round(kospi_6m, 2) if kospi_6m else None,
        'universe_sigma_median_pct': round(univ_sigma_median, 1),
        'n_holdings': len(top),
        'holdings': top,
        'model_portfolio_n': len(holdings),
        'excluded_ma60': sorted(exit_set),
    }
    with open(OUT_FINAL, 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f'  저장: {OUT_FINAL}')

    print(f'[5/5] 최종 포트폴리오 문서 작성…')
    L = []
    L.append(f'# 최종 포트폴리오 ({args.top_n}종목, 추세추종 기술분석 통합)')
    L.append('')
    L.append(f'**생성:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | **기준:** {sig_asof}')
    L.append(f'**투자자:** {pf["investor_type"]} (equity {pf["equity_pct"]}%) | **레짐:** {pf["regime"]}')
    L.append(f'**산식:** 결합점수 = {w_a:.1f} × 매력도(0~100) + {w_t:.1f} × 기술점수(0~100)')
    L.append(f'**KOSPI 6M:** {kospi_6m:.2f}% | **universe σ median:** {univ_sigma_median:.1f}%')
    L.append('')
    L.append('## 최종 보유 종목')
    L.append('')
    L.append('| # | 티커 | 종목명 | 섹터 | 비중 | 자본대비 | 매력도 | 기술점수 | 결합 | 매칭 | 시총 |')
    L.append('|---:|---|---|---|---:|---:|---:|---:|---:|---|---|')
    for i, c in enumerate(top, 1):
        ideas = '+'.join([f"#{a}" for a in (c.get('matched_ideas') or [])]) or ('default' if c['is_default_pick'] else '-')
        L.append(f"| {i} | {c['ticker']} | {c['name']} | {c['sector']} | **{c['weight_pct']:.2f}%** | {c['capital_weight_pct']:.2f}% | {c['attractiveness']:.1f} | **{c['tech_score']:.1f}** | {c['combined']:.1f} | {ideas} | {c['size_tier']} |")
    L.append('')

    L.append('## 기술점수 breakdown (Top 종목)')
    L.append('')
    L.append('| 티커 | 정배열(20) | MA60이격(15) | 모멘텀(15) | 거래량(10) | 52w위치(15) | RSI(10) | 변동성(15) | **합계** |')
    L.append('|---|---:|---:|---:|---:|---:|---:|---:|---:|')
    for c in top:
        b = c.get('tech_breakdown') or {}
        L.append(f"| {c['ticker']} | {b.get('alignment(20)','-')} | {b.get('ma60_margin(15)','-')} | {b.get('momentum(15)','-')} | {b.get('volume(10)','-')} | {b.get('pos_52w(15)','-')} | {b.get('rsi(10)','-')} | {b.get('volatility(15)','-')} | **{c['tech_score']:.1f}** |")
    L.append('')

    L.append('## 핵심 지표 (Top 종목)')
    L.append('')
    L.append('| 티커 | 종가(원) | MA60 | 이격% | 6M수익률 | RSI(14) | 거래량비 | 52w위치 | σ연환산 |')
    L.append('|---|---:|---:|---:|---:|---:|---:|---:|---:|')
    for c in top:
        ind = c.get('indicators') or {}
        L.append(f"| {c['ticker']} | {ind.get('close',0):,.0f} | {ind.get('ma60','-')} | {ind.get('ma60_margin_pct','-')}% | {ind.get('ret_6m_pct','-')}% | {ind.get('rsi14','-')} | {ind.get('vol_5d_to_20d_ratio','-')}x | {ind.get('pos_52w_pct','-')}% | {ind.get('sigma_annual_pct','-')}% |")
    L.append('')

    L.append('## 제외 사유')
    L.append('')
    L.append(f'- 모델 포트폴리오 {len(holdings)}종목 중 **{len(holdings) - len(top)}종목 제외**')
    L.append(f'- 제외 사유: MA60 이탈 {len(exit_set)}종목 + 결합 점수 하위 {len(holdings) - len(top) - len(exit_set)}종목')
    L.append('')
    L.append('### MA60 이탈 (자동 제외)')
    if exit_set:
        for t in sorted(exit_set):
            tinfo = tech_results.get(t, {})
            ind = tinfo.get('indicators') or {}
            L.append(f"- {t} {tinfo.get('name','?')} : 종가 {ind.get('close','-')} < MA60 {ind.get('ma60','-')}")
    L.append('')

    L.append('## 운영 가이드')
    L.append('')
    L.append('- **매일 종가 후** `daily_signals.py` 실행 → 진입가·청산가 도달 여부, 신규 진입 후보 검토')
    L.append('- **분할 매수**: 1차 50% / 2차 25% (-5%, MA60 위) / 3차 25% (-10%, MA60 위)')
    L.append('- **청산 트리거**: MA60 하향 / 트레일링 진입가 ×0.85 / 결합 점수 30점 이하 강등')
    L.append('- **주 1회 (금요일)**: 모델 포트폴리오 + 최종 5~10종목 재산출 (시그널 + 매력도 + 기술점수 종합)')
    L.append('')
    L.append('---')
    L.append('')
    L.append('*면책: 본 자료는 정보 제공 목적의 분석 결과이며 투자 권유가 아닙니다.*')

    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print(f'  저장: {OUT_MD}')
    print()
    print(f'=== 최종 포트폴리오 {len(top)}종목 ===')
    for i, c in enumerate(top, 1):
        print(f"  {i} {c['ticker']} {c['name']:<14} 비중 {c['weight_pct']:>5.2f}%  매력 {c['attractiveness']:>5.1f}  기술 {c['tech_score']:>5.1f}  결합 {c['combined']:>5.1f}")


if __name__ == '__main__':
    main()
