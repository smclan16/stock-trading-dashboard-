#!/usr/bin/env python3
"""신규 진입 매매 계획서 생성기 (HITL)

입력:
  --capital   투자 자본 (만원 단위, 예: 10000 = 1억원)
  --skip-exit  완전 청산(MA60 하향) 종목을 매수 보류 처리 (기본 True)

출력:
  _workspace/08_signals/entry_order_plan.md  (사용자 검토용)
  _workspace/08_signals/entry_order_plan.json (구조화)
"""
import sys, os, json, argparse, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from lib.datasource import KRXMarket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PF_PATH_FULL = os.path.join(ROOT, '07_portfolio', 'portfolio.json')
PF_PATH_FINAL = os.path.join(ROOT, '08_signals', 'final_portfolio.json')
SIG_PATH = os.path.join(ROOT, '08_signals', 'signals.json')
OUT_MD = os.path.join(ROOT, '08_signals', 'entry_order_plan.md')
OUT_JSON = os.path.join(ROOT, '08_signals', 'entry_order_plan.json')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--capital-eok', type=float, required=True, help='투자 자본 (억원)')
    ap.add_argument('--no-skip-exit', action='store_true', help='청산 시그널 종목도 매수 (기본: 보류)')
    ap.add_argument('--use-final', action='store_true', help='final_portfolio.json (Top 5~10) 사용 (기본: portfolio.json 48종목)')
    args = ap.parse_args()

    cap_eok = args.capital_eok
    skip_exit = not args.no_skip_exit

    print(f'[1/4] 입력 로드…')
    pf_path = PF_PATH_FINAL if args.use_final else PF_PATH_FULL
    pf = json.load(open(pf_path, encoding='utf-8'))
    print(f'  source: {pf_path}')
    sigs = json.load(open(SIG_PATH, encoding='utf-8'))
    holdings = pf['holdings']
    as_of = pf['as_of']
    investor = pf['investor_type']
    equity_pct = pf['equity_pct']
    print(f'  as_of={as_of} | {investor} equity={equity_pct}% | 보유 {len(holdings)}종목')

    # exit 종목 (MA60 하향)
    exit_set = set()
    exit_detail = {}
    for s in sigs['signals']:
        if 'exit' in s.get('signal_type', '').lower():
            t = s['ticker']
            exit_set.add(t)
            exit_detail[t] = {
                'name': s.get('name'),
                'close': s.get('current_close'),
                'reason': s.get('reason') or s.get('trigger'),
            }
    print(f'  청산(보류) 시그널: {len(exit_set)}종목')

    # 종가 (signals 우선, 부족분 KRX 일괄)
    closes = {s['ticker']: s['current_close'] for s in sigs['signals'] if s.get('current_close')}
    missing = [h['ticker'] for h in holdings if h['ticker'] not in closes]
    if missing:
        print(f'[2/4] KRX 종가 일괄 조회 ({len(missing)}종목 누락분)…')
        krx = KRXMarket()
        # 신호 기준일 우선 (시뮬레이션 KRX는 미래일자 미보유 가능)
        sig_asof = sigs.get('as_of') or sigs.get('rebalance_basis') or as_of
        ymd_candidates = [
            (sig_asof or '').replace('-', '')[:8],
            (as_of or '').replace('-', '')[:8],
            krx.latest_trading_date() if hasattr(krx, 'latest_trading_date') else None,
        ]
        for ymd in ymd_candidates:
            if not ymd:
                continue
            try:
                daily = krx.daily(ymd)
            except Exception as e:
                print(f'  daily({ymd}) 실패: {e}')
                continue
            for t in missing:
                if t in closes:
                    continue
                if t in daily and daily[t].get('close'):
                    closes[t] = daily[t]['close']
            still = [t for t in missing if t not in closes]
            print(f'  daily({ymd}) → 잔여 {len(still)}종목')
            if not still:
                break
        still_missing = [t for t in missing if t not in closes]
        if still_missing:
            print(f'  ⚠️ 종가 미확인 {len(still_missing)}종목: {still_missing}')
    else:
        print(f'[2/4] 종가 보유 (signals 캐시 활용)')

    print(f'[3/4] 매매 계획 산출 (자본 {cap_eok}억원, 1차 50% 즉시)…')
    cap_won = int(cap_eok * 100_000_000)

    rows_buy = []
    rows_hold = []
    rows_no_close = []
    buy_won_total = 0
    buy_1st_won_total = 0
    hold_won_total = 0

    for h in sorted(holdings, key=lambda x: -x['capital_weight_pct']):
        t = h['ticker']
        name = h['name']
        cw = h['capital_weight_pct']
        budget = int(round(cap_won * cw / 100))  # capital 비중 그대로 사용

        if skip_exit and t in exit_set:
            rows_hold.append({
                'ticker': t, 'name': name, 'capital_weight_pct': cw, 'budget_won': budget,
                'reason': exit_detail.get(t, {}).get('reason', 'MA60 하향'),
                'close': exit_detail.get(t, {}).get('close') or closes.get(t),
            })
            hold_won_total += budget
            continue

        close = closes.get(t)
        if not close:
            rows_no_close.append({
                'ticker': t, 'name': name, 'capital_weight_pct': cw, 'budget_won': budget,
            })
            continue

        # v6 분할: 1차 75%, 2차 15% (-7%), 3차 10% (-12%) — Buy & Hold 지향
        first_won = int(budget * 0.75)
        second_won = int(budget * 0.15)
        third_won = budget - first_won - second_won
        cp = int(close)
        # 1차 주식수: 75% 한도로 못 사면 budget 전체로 1주 가능 여부 확인
        shares_1 = first_won // cp
        note = ''
        if shares_1 == 0 and budget >= cp:
            shares_1 = 1
            note = '1주 단가 > 1차 한도 → 1차에 1주 매수 (분할 불가)'
        elif shares_1 == 0:
            note = '1주 단가 > 자본 한도 → 매수 불가 (다음 사이클 평가)'
        trigger_2 = round(close * 0.93, -1)  # -7% (v6)
        trigger_3 = round(close * 0.88, -1)  # -12% (v6)
        rows_buy.append({
            'ticker': t, 'name': name,
            'sector': h.get('sector'),
            'capital_weight_pct': cw,
            'attractiveness': h.get('attractiveness'),
            'matched_ideas': h.get('matched_ideas'),
            'is_default_pick': h.get('is_default_pick', False),
            'size_tier': h.get('size_tier'),
            'close': close,
            'budget_won': budget,
            'first_won': first_won, 'first_shares': shares_1,
            'second_won': second_won, 'second_trigger_close': trigger_2,
            'third_won': third_won, 'third_trigger_close': trigger_3,
            'first_actual_won': shares_1 * cp,
            'note': note,
        })
        buy_won_total += budget
        buy_1st_won_total += shares_1 * int(close)

    # === 문서 작성 ===
    print(f'[4/4] 문서 작성…')
    L = []
    L.append(f'# 신규 진입 매매 계획서 (HITL 검토용)')
    L.append('')
    L.append(f'**생성:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} | **기준:** v4 portfolio ({as_of}) + signals')
    L.append(f'**투자자:** {investor} (equity {equity_pct}%) | **레짐:** {pf.get("regime", "?")}')
    L.append('')
    L.append('## 요약')
    L.append('')
    L.append(f'- **투자 자본**: {cap_eok}억원 (= {cap_won:,}원)')
    L.append(f'- **매수 대상**: {len(rows_buy)}종목 / 보유 후보 {len(holdings)}종목')
    L.append(f'- **매수 보류 (MA60 하향)**: {len(rows_hold)}종목, 합 {hold_won_total:,}원')
    L.append(f'- **즉시 1차 매수 (50% 분할)**: {buy_1st_won_total:,}원 ({buy_1st_won_total/cap_won*100:.1f}%)')
    L.append(f'- **총 매수 한도 (1+2+3차)**: {buy_won_total:,}원 ({buy_won_total/cap_won*100:.1f}%, equity {equity_pct}% 반영)')
    if rows_no_close:
        L.append(f'- ⚠️ **종가 미확인 (수동 확인 필요)**: {len(rows_no_close)}종목')

    L.append('')
    L.append('## 즉시 1차 매수 주문 (50% 분할)')
    L.append('')
    L.append('| # | 티커 | 종목명 | 섹터 | 자본비중 | 종가(원) | 1차 매수액(원) | 1차 주식수 | 2차 발동가 | 3차 발동가 | 매칭 | 매력도 | 비고 |')
    L.append('|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|')
    for i, r in enumerate(rows_buy, 1):
        ideas = '+'.join([f"#{a}" for a in (r.get('matched_ideas') or [])]) or ('default' if r['is_default_pick'] else '-')
        note = r.get('note', '') or ''
        L.append(f"| {i} | {r['ticker']} | {r['name']} | {r['sector']} | {r['capital_weight_pct']:.2f}% | {r['close']:,.0f} | {r['first_actual_won']:,} | {r['first_shares']:,} | {r['second_trigger_close']:,.0f} | {r['third_trigger_close']:,.0f} | {ideas} | {r.get('attractiveness',0):.1f} | {note} |")
    L.append('')

    if rows_hold:
        L.append('## 매수 보류 종목 (MA60 하향, 추세 회복 후 재평가)')
        L.append('')
        L.append('| 티커 | 종목명 | 자본비중 | 미집행 한도(원) | 사유 |')
        L.append('|---|---|---:|---:|---|')
        for r in rows_hold:
            L.append(f"| {r['ticker']} | {r['name']} | {r['capital_weight_pct']:.2f}% | {r['budget_won']:,} | {r['reason'] or 'MA60 하향'} |")
        L.append('')
        L.append(f'**보류분 합:** {hold_won_total:,}원 (현금 유지, 다음 사이클 재평가)')
        L.append('')

    if rows_no_close:
        L.append('## ⚠️ 종가 미확인 — 수동 확인 후 매수')
        L.append('')
        L.append('| 티커 | 종목명 | 자본비중 | 한도(원) |')
        L.append('|---|---|---:|---:|')
        for r in rows_no_close:
            L.append(f"| {r['ticker']} | {r['name']} | {r['capital_weight_pct']:.2f}% | {r['budget_won']:,} |")
        L.append('')

    L.append('## 분할 매수 규칙 (모든 종목 공통)')
    L.append('')
    L.append('- **1차 (50%)**: 즉시 매수 (다음 거래일 시가)')
    L.append('- **2차 (25%)**: 1차 진입가 대비 -5% 도달 + MA(60) 위 유지 시')
    L.append('- **3차 (25%)**: 1차 진입가 대비 -10% 도달 + MA(60) 위 유지 시')
    L.append('')
    L.append('## 청산 트리거 (보유 후) — v7 2단계')
    L.append('')
    L.append('- **1단계** MA(20) 하향 → **50% 청산** (조기 대응, 잔여 50% 보유)')
    L.append('- **2단계** MA(60) 하향 → **100% 청산** (또는 1단계 후 잔여 50% 청산)')
    L.append('- 추세 유지 시 (MA20 위 복귀) → 잔여 50% 보유 유지, 신규 추가 매수는 다음 사이클')
    L.append('- 주 1회 (금요일) 시그널 재평가, 포트폴리오 변경 시 청산/추가')
    L.append('')
    L.append('## 집행 가이드')
    L.append('')
    L.append('1. 본 계획서는 정보 제공용. **실제 주문 입력은 사용자가 직접 증권사 HTS/MTS에서 수행**.')
    L.append('2. 시장가 매수 권장 X — 호가 확인 후 **지정가 또는 분할 시장가** 활용.')
    L.append('3. 주문 후 체결 가격을 다음 사이클 분할 매수 발동가 계산에 사용 (위 발동가는 종가 기준 가이드).')
    L.append('4. 신용 매수 활용 시 증권사 신용 한도·이자 별도 확인.')
    L.append('')
    L.append('---')
    L.append('')
    L.append('*면책: 본 계획서는 정보 제공 목적의 분석 결과이며 투자 권유가 아닙니다. 모든 매매 결정의 책임은 투자자 본인에게 있습니다.*')

    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')

    summary = {
        'as_of': as_of,
        'generated_at': datetime.datetime.now().isoformat(),
        'investor_type': investor,
        'equity_pct': equity_pct,
        'capital_won': cap_won,
        'capital_eok': cap_eok,
        'skip_exit_signals': skip_exit,
        'n_buy': len(rows_buy),
        'n_hold': len(rows_hold),
        'n_no_close': len(rows_no_close),
        'buy_won_total': buy_won_total,
        'buy_1st_won_total': buy_1st_won_total,
        'hold_won_total': hold_won_total,
        'orders_1st': rows_buy,
        'on_hold': rows_hold,
        'no_close': rows_no_close,
    }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f'  저장: {OUT_MD}')
    print(f'         {OUT_JSON}')
    print()
    print(f'=== 요약 ===')
    print(f'  매수 {len(rows_buy)}종목 / 보류 {len(rows_hold)}종목 / 종가미확인 {len(rows_no_close)}종목')
    print(f'  1차 즉시 매수액: {buy_1st_won_total:,}원')
    print(f'  총 매수 한도   : {buy_won_total:,}원')
    print(f'  보류분 (현금)  : {hold_won_total:,}원')


if __name__ == '__main__':
    main()
