#!/usr/bin/env python3
"""일별 시그널 산출기 (HITL 큐)

매일 종가 후 실행하여 final_portfolio.json 종목에 대한 액션 시그널 산출.

시그널 종류:
  · ENTRY_1ST            : 최초 1차 진입 (75%, MA60 위 첫 진입)
  · ADD_2ND              : 2차 분할 추가 (1차 -7% + MA60 위, 15%)
  · ADD_3RD              : 3차 분할 추가 (1차 -12% + MA60 위, 10%)
  · EXIT_MA20_PARTIAL    : MA20 하향 → 50% 청산 (조기 대응, exit_stage 0)
  · EXIT_MA60_FULL       : MA60 하향 → 100% 청산 (exit_stage 0)
  · EXIT_MA60_REMAINDER  : MA20 청산 후 MA60 하향 → 잔여 50% 청산
  · WATCH                : 액션 없음, 모니터링

입력:
  _workspace/08_signals/final_portfolio.json
  _workspace/08_signals/positions.json      (보유 포지션 상태, 매일 갱신)

출력:
  _workspace/08_signals/daily_signal_queue.md   (HITL 검토용)
  _workspace/08_signals/daily_signals.json     (구조화)
"""
import sys, os, json, argparse, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from lib.datasource import KRXMarket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINAL_PATH = os.path.join(ROOT, '08_signals', 'final_portfolio.json')
THEME_PATH = os.path.join(ROOT, '08_signals', 'theme_portfolio.json')
MATCH_PATH = os.path.join(ROOT, '05_matching', 'matching_matrix.json')
TECH_PATH = os.path.join(ROOT, '08_signals', 'technical_scores.json')
POS_PATH = os.path.join(ROOT, '08_signals', 'positions.json')
OUT_QUEUE = os.path.join(ROOT, '08_signals', 'daily_signal_queue.md')
OUT_JSON = os.path.join(ROOT, '08_signals', 'daily_signals.json')


def load_positions():
    if os.path.exists(POS_PATH):
        return json.load(open(POS_PATH, encoding='utf-8'))
    return {'as_of': None, 'positions': {}}


def save_positions(pos):
    with open(POS_PATH, 'w', encoding='utf-8') as f:
        json.dump(pos, f, ensure_ascii=False, indent=2)


def calc_ma(prices, n):
    if len(prices) < n: return None
    return sum(prices[-n:]) / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', default=None, help='YYYYMMDD (기본: 최근 거래일)')
    ap.add_argument('--days', type=int, default=80, help='MA60 계산용 시계열 길이')
    args = ap.parse_args()

    print(f'[1/4] 입력 로드…')
    if not os.path.exists(FINAL_PATH):
        print(f'  ERROR: {FINAL_PATH} 없음. compute_technical_scores.py 먼저 실행 필요')
        sys.exit(1)
    fp = json.load(open(FINAL_PATH, encoding='utf-8'))
    holdings = fp['holdings']
    # 매칭+기술점수 (종목 교체 후보용)
    match_map = {}
    if os.path.exists(MATCH_PATH):
        mm = json.load(open(MATCH_PATH, encoding='utf-8'))
        for entry in mm.get('matrix', []):
            match_map[entry['idea_id']] = entry.get('matched_tickers', [])
    tech_map = {}
    if os.path.exists(TECH_PATH):
        td = json.load(open(TECH_PATH, encoding='utf-8'))
        tech_map = td.get('scores', {})
    pos_state = load_positions()
    positions = pos_state.get('positions', {})
    print(f'  최종 포트: {len(holdings)}종목 | 현재 보유: {len(positions)}건 | 테마매핑: {len(match_map)} | 기술점수: {len(tech_map)}')

    krx = KRXMarket()
    asof = args.asof or krx.latest_trading_date()
    print(f'[2/4] KRX 데이터 ({asof}, {args.days}영업일)…')
    dates = krx.trading_dates(asof, args.days)
    dates.sort()  # 오래된→최신

    # KOSPI 6M for 강세장 필터 (v8)
    try:
        kospi_hist = krx.kospi_index_history(asof, 26)
        kospi_closes = [r['close'] for r in kospi_hist if 'close' in r]
        kospi_6m_pct = (kospi_closes[-1] / kospi_closes[0] - 1) * 100 if len(kospi_closes) >= 2 else None
    except Exception:
        kospi_6m_pct = None
    bull_market = kospi_6m_pct is not None and kospi_6m_pct >= 0
    print(f'  KOSPI 6M: {kospi_6m_pct:.2f}% → {"강세장(MA20 청산 무효)" if bull_market else "약세장(MA20 청산 발효)"}')

    # 종목별 시계열
    target_tickers = {h['ticker'] for h in holdings}
    price_map = {t: [] for t in target_tickers}
    for d in dates:
        try:
            day = krx.daily(d)
        except Exception:
            continue
        for t in target_tickers:
            if t in day and day[t].get('close'):
                price_map[t].append(day[t]['close'])

    # 현재 보유 ticker → 교체 후보 검색 함수
    held_tickers = set(h['ticker'] for h in holdings)

    def find_replacement(exit_ticker, idea_ids):
        """exit 종목의 테마에서 차순위 후보 (MA60 위 + 미보유)"""
        candidates = []
        for iid in idea_ids or []:
            for m in match_map.get(iid, []):
                tkr = m['ticker']
                if tkr == exit_ticker or tkr in held_tickers:
                    continue
                t_info = tech_map.get(tkr) or {}
                ind = t_info.get('indicators') or {}
                close = ind.get('close')
                ma60 = ind.get('ma60')
                if close and ma60 and close > ma60:
                    candidates.append({
                        'ticker': tkr, 'name': m.get('name'),
                        'intensity': m.get('intensity'),
                        'close': close, 'ma60': ma60,
                        'tech_score': t_info.get('tech_score'),
                        'idea_id': iid,
                    })
        candidates.sort(key=lambda x: -(x.get('tech_score') or 0))
        return candidates[:3]  # 상위 3개

    print(f'[3/4] 종목별 시그널 산출…')
    signals = []
    for h in holdings:
        t = h['ticker']
        name = h['name']
        ps = price_map[t]
        if len(ps) < 60:
            signals.append({'ticker': t, 'name': name, 'signal': 'WATCH',
                            'reason': f'시계열 부족 {len(ps)}일'})
            continue
        close = ps[-1]
        ma20 = calc_ma(ps, 20)
        ma60 = calc_ma(ps, 60)
        pos = positions.get(t)

        # 보유 중인 경우
        if pos:
            entry = pos['entry_price_1st']
            shares_held = pos.get('shares_total', 0)
            phase = pos.get('phase', 1)  # 1/2/3차 매수 단계
            exit_stage = pos.get('exit_stage', 0)  # 0: 정상보유 / 1: MA20 청산 후 잔여 50%

            # v7 2단계 청산: MA60 하향 (전량 또는 잔여) → MA20 하향 (50%, stage 0만)
            if close < ma60:
                repls = find_replacement(t, h.get('matched_ideas') or [])
                if exit_stage == 0:
                    signals.append({'ticker': t, 'name': name, 'signal': 'EXIT_MA60_FULL',
                                    'action': '100% 청산', 'shares_to_sell': shares_held,
                                    'close': close, 'ma60': round(ma60, 1),
                                    'reason': f'MA60 하향 (종가 {close:,.0f} < MA60 {ma60:,.0f})',
                                    'entry_1st': entry, 'pnl_pct': round((close / entry - 1) * 100, 2),
                                    'replacement_candidates': repls,
                                    'theme_ids': h.get('matched_ideas')})
                else:
                    signals.append({'ticker': t, 'name': name, 'signal': 'EXIT_MA60_REMAINDER',
                                    'action': '잔여 50% 청산', 'shares_to_sell': shares_held,
                                    'close': close, 'ma60': round(ma60, 1),
                                    'reason': f'MA20 청산 후 MA60도 하향 (종가 {close:,.0f} < MA60 {ma60:,.0f})',
                                    'entry_1st': entry, 'pnl_pct': round((close / entry - 1) * 100, 2),
                                    'replacement_candidates': repls,
                                    'theme_ids': h.get('matched_ideas')})
            elif exit_stage == 0 and ma20 and close < ma20 and not bull_market:
                # v8: 강세장 KOSPI 6M ≥ 0%면 MA20 청산 무효 (추세 유지)
                signals.append({'ticker': t, 'name': name, 'signal': 'EXIT_MA20_PARTIAL',
                                'action': '50% 청산 (조기 대응, 약세장)', 'shares_to_sell': int(shares_held * 0.5),
                                'close': close, 'ma20': round(ma20, 1), 'ma60': round(ma60, 1),
                                'reason': f'MA20 하향 + 약세장 KOSPI 6M {kospi_6m_pct:+.1f}% (종가 {close:,.0f} < MA20 {ma20:,.0f})',
                                'entry_1st': entry, 'pnl_pct': round((close / entry - 1) * 100, 2),
                                'theme_ids': h.get('matched_ideas')})
            # v6 2차 추가 (-7%, 15%) — exit_stage 0인 경우만
            elif exit_stage == 0 and phase < 2 and close <= entry * 0.93 and close > ma60:
                signals.append({'ticker': t, 'name': name, 'signal': 'ADD_2ND',
                                'action': '2차 15% 추가 매수',
                                'close': close, 'entry_1st': entry, 'trigger': round(entry * 0.93, 0),
                                'reason': f'1차 -7% 도달 + MA60 위 ({close:,.0f} ≤ {entry*0.93:,.0f})',
                                'budget_won': pos.get('second_budget_won')})
            # v6 3차 추가 (-12%, 10%) — exit_stage 0인 경우만
            elif exit_stage == 0 and phase < 3 and close <= entry * 0.88 and close > ma60:
                signals.append({'ticker': t, 'name': name, 'signal': 'ADD_3RD',
                                'action': '3차 10% 추가 매수',
                                'close': close, 'entry_1st': entry, 'trigger': round(entry * 0.88, 0),
                                'reason': f'1차 -12% 도달 + MA60 위 ({close:,.0f} ≤ {entry*0.88:,.0f})',
                                'budget_won': pos.get('third_budget_won')})
            else:
                signals.append({'ticker': t, 'name': name, 'signal': 'WATCH',
                                'phase': phase, 'close': close, 'ma60': round(ma60, 1),
                                'entry_1st': entry,
                                'pnl_pct': round((close / entry - 1) * 100, 2)})
        # 미보유 — 진입 후보
        else:
            if close > ma60:
                signals.append({'ticker': t, 'name': name, 'signal': 'ENTRY_1ST',
                                'action': '1차 75% 진입 (지정가 권장)',
                                'close': close, 'ma60': round(ma60, 1),
                                'weight_pct': h['weight_pct'],
                                'capital_weight_pct': h['capital_weight_pct'],
                                'reason': f'MA60 위 첫 진입 (종가 {close:,.0f} > MA60 {ma60:,.0f})'})
            else:
                signals.append({'ticker': t, 'name': name, 'signal': 'WATCH',
                                'close': close, 'ma60': round(ma60, 1),
                                'reason': f'MA60 아래, 추세 대기 ({close:,.0f} < MA60 {ma60:,.0f})'})

    # 카운트
    counts = {}
    for s in signals:
        counts[s['signal']] = counts.get(s['signal'], 0) + 1

    print(f'[4/4] HITL 큐 작성…')
    L = []
    L.append(f'# 일일 시그널 큐 ({asof})')
    L.append('')
    L.append(f'**생성:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}')
    L.append(f'**최종 포트:** {len(holdings)}종목 | **현재 보유 포지션:** {len(positions)}건')
    L.append('')
    L.append('## 시그널 요약')
    L.append('')
    L.append('| 시그널 | 카운트 | 의미 |')
    L.append('|---|---:|---|')
    desc = {
        'ENTRY_1ST': '✅ 1차 진입 (50% 즉시)',
        'ADD_2ND': '➕ 2차 추가 (25%)',
        'ADD_3RD': '➕ 3차 추가 (25%)',
        'EXIT_MA60': '🔴 MA60 하향 → 100% 청산',
        'EXIT_TRAILING': '🔴 트레일링 -15% → 100% 청산',
        'WATCH': '👁 관찰 (액션 없음)',
    }
    for sig, n in sorted(counts.items(), key=lambda x: -x[1]):
        L.append(f'| {sig} | {n} | {desc.get(sig, "?")} |')
    L.append('')

    # 액션 필요 시그널 우선 표시
    action_sigs = [s for s in signals if s['signal'] != 'WATCH']
    if action_sigs:
        L.append(f'## 🚨 액션 시그널 ({len(action_sigs)}건) — 사용자 승인 필요')
        L.append('')
        L.append('| 시그널 | 티커 | 종목 | 종가 | 액션 | 사유 |')
        L.append('|---|---|---|---:|---|---|')
        for s in action_sigs:
            L.append(f"| {s['signal']} | {s['ticker']} | {s['name']} | {s.get('close',0):,.0f} | {s.get('action','-')} | {s.get('reason','')} |")
        L.append('')

        # 청산 시그널에 대한 교체 후보
        exit_sigs = [s for s in action_sigs if 'EXIT' in s['signal']]
        if exit_sigs:
            L.append('### 청산 종목 교체 후보 (같은 테마, MA60 위)')
            L.append('')
            for es in exit_sigs:
                repls = es.get('replacement_candidates') or []
                tids = es.get('theme_ids') or []
                L.append(f"**{es['ticker']} {es['name']}** (테마 #{','.join(map(str, tids))}) 청산 시:")
                if repls:
                    L.append('')
                    L.append('| 후보 | 종목 | 종가 | MA60 | 기술점수 | 매칭강도 |')
                    L.append('|---|---|---:|---:|---:|---|')
                    for r in repls:
                        ts = r.get('tech_score')
                        ts_str = f"{ts:.0f}" if ts is not None else '-'
                        L.append(f"| 1위 → 3위 | {r['ticker']} {r['name']} | {r['close']:,.0f} | {r['ma60']:,.0f} | {ts_str} | {r['intensity']} |")
                else:
                    L.append('  → 동일 테마 내 매수 시그널 종목 없음. **현금 보유 또는 테마 비중 리밸런싱**.')
                L.append('')

    # 관찰 (간략)
    watch_sigs = [s for s in signals if s['signal'] == 'WATCH']
    if watch_sigs:
        L.append(f'## 관찰 {len(watch_sigs)}종목')
        L.append('')
        L.append('| 티커 | 종목 | 종가 | MA60 | 상태 |')
        L.append('|---|---|---:|---:|---|')
        for s in watch_sigs:
            phase_str = f"P{s['phase']} pnl{s.get('pnl_pct',0):+.1f}%" if 'phase' in s else '미보유'
            L.append(f"| {s['ticker']} | {s['name']} | {s.get('close',0):,.0f} | {s.get('ma60','-')} | {phase_str} |")
        L.append('')

    L.append('## HITL 승인 가이드')
    L.append('')
    L.append('1. **액션 시그널** 표의 매매 액션을 검토')
    L.append('2. 승인 종목만 증권사 HTS/MTS에서 직접 주문 (지정가 권장)')
    L.append('3. 체결 후 `positions.json` 수동 또는 자동 갱신 → 분할 매수 발동가 추적')
    L.append('4. 다음 거래일 종가 후 본 스크립트 재실행')
    L.append('')
    L.append('---')
    L.append('*면책: 본 시그널은 정보 제공 목적이며 투자 권유가 아닙니다.*')

    with open(OUT_QUEUE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({'as_of': asof, 'generated_at': datetime.datetime.now().isoformat(),
                   'counts': counts, 'signals': signals}, f, ensure_ascii=False, indent=2)

    print(f'  저장: {OUT_QUEUE}')
    print(f'         {OUT_JSON}')
    print()
    print(f'=== 일일 시그널 요약 ({asof}) ===')
    for sig, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {sig:<15}: {n:>3}건')


if __name__ == '__main__':
    main()
