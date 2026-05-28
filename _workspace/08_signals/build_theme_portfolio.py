#!/usr/bin/env python3
"""테마 비중 골격 유지 포트폴리오 생성기

원칙:
  1) 4단계 ideas.json 테마 allocation_pct가 골격
  2) 상위 N개 테마 선택 (매칭 종목 있는 테마만)
  3) 미선택 테마 비중을 상위 N개에 비례 재분배 (합 90% 유지)
  4) 각 테마에서 결합점수(매력도 × 0.5 + 기술점수 × 0.5) 최고 1종 선정 + MA60 위
  5) 종목 중복 시: 비중 큰 테마 우선 배정, 작은 테마는 차순위
  6) default_picks 4종 (합 10%) 그대로
  7) 총 N개 핵심 테마 + 4 default = N+4 종목

산출:
  _workspace/08_signals/theme_portfolio.json
  _workspace/08_signals/theme_portfolio.md / .docx
"""
import sys, os, json, argparse, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDEAS_PATH = os.path.join(ROOT, '04_ideas', 'ideas.json')
MATCH_PATH = os.path.join(ROOT, '05_matching', 'matching_matrix.json')
RESEARCH_PATH = os.path.join(ROOT, '06_research', 'research_scores.json')
TECH_PATH = os.path.join(ROOT, '08_signals', 'technical_scores.json')
SIG_PATH = os.path.join(ROOT, '08_signals', 'signals.json')
PF_PATH = os.path.join(ROOT, '07_portfolio', 'portfolio.json')
OUT_JSON = os.path.join(ROOT, '08_signals', 'theme_portfolio.json')
OUT_MD = os.path.join(ROOT, '08_signals', 'theme_portfolio.md')


SIZE_CAPS = {'large': 15, 'mid': 10, 'small': 5}  # 시총별 단일 종목 cap (%)


def size_cap_for(tier):
    return SIZE_CAPS.get(tier or 'mid', 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n-themes', type=int, default=8, help='핵심 테마 수 (기본 8)')
    ap.add_argument('--w-tech', type=float, default=0.5, help='기술점수 가중 (0~1)')
    ap.add_argument('--rebalance-on-shortfall', action='store_true')
    ap.add_argument('--pf-file', default=None, help='portfolio.json 경로 override (다중 유형용)')
    ap.add_argument('--output-suffix', default='', help='산출 파일명 suffix (예: _안정형)')
    args = ap.parse_args()
    w_a = 1 - args.w_tech
    w_t = args.w_tech

    print(f'[1/5] 입력 로드…')
    ideas_data = json.load(open(IDEAS_PATH, encoding='utf-8'))
    match_data = json.load(open(MATCH_PATH, encoding='utf-8'))
    research_data = json.load(open(RESEARCH_PATH, encoding='utf-8'))
    tech_data = json.load(open(TECH_PATH, encoding='utf-8'))
    sigs = json.load(open(SIG_PATH, encoding='utf-8'))
    pf_path = args.pf_file or PF_PATH
    pf = json.load(open(pf_path, encoding='utf-8'))

    ideas = ideas_data['ideas']
    default_picks = ideas_data.get('default_picks', {})
    default_alloc = default_picks.get('allocation_pct', 10.0)
    default_list = default_picks.get('picks', [])
    print(f'  4단계 테마: {len(ideas)}개 / default {len(default_list)}종 ({default_alloc}%)')

    # 매칭 종목 (idea_id → list of {ticker, name, intensity})
    match_by_idea = {}
    for entry in match_data['matrix']:
        iid = entry['idea_id']
        match_by_idea[iid] = entry.get('matched_tickers', [])

    # 6축 매력도 (research_scores)
    research_rank = research_data.get('ranking', research_data) if isinstance(research_data, dict) else research_data
    if isinstance(research_rank, dict) and 'ranking' in research_rank:
        research_rank = research_rank['ranking']
    attr_map = {}
    rows = research_data.get('rows', research_data.get('scores', []))
    for r in rows:
        if isinstance(r, dict) and 'ticker' in r:
            attr_map[r['ticker']] = r.get('total_score') or r.get('total', 0)

    # 기술점수 (technical_scores)
    tech_scores = tech_data.get('scores', {})

    # MA60 이탈 (시그널)
    exit_set = set(s['ticker'] for s in sigs['signals'] if 'exit' in s.get('signal_type', '').lower())

    # 종목 기본정보 (portfolio.json)
    pf_meta = {h['ticker']: h for h in pf['holdings']}

    print(f'  매력도 매핑: {len(attr_map)}종 | 기술점수: {len(tech_scores)}종 | MA60이탈: {len(exit_set)}')

    print(f'[2/5] 테마 정렬 + 상위 {args.n_themes}개 선정…')
    # 매칭 있고 비중 > 0인 테마만 (id가 None인 경우 idx 사용)
    theme_list = []
    for idx, it in enumerate(ideas):
        iid = it.get('id') or it.get('idea_id') or (idx + 1)
        theme_list.append({
            'idea_id': iid,
            'theme': it.get('theme') or it.get('name') or f'테마{iid}',
            'allocation_pct': it.get('allocation_pct', 0) or 0,
            'category': it.get('category', '?'),
            'matched_tickers': match_by_idea.get(iid, []),
        })
    # 매칭 종목 있고 비중 > 0
    theme_list = [t for t in theme_list if t['allocation_pct'] > 0 and t['matched_tickers']]
    theme_list.sort(key=lambda x: -x['allocation_pct'])
    print(f'  매칭+비중 보유 테마: {len(theme_list)}개')
    for i, t in enumerate(theme_list):
        mark = '⭐' if i < args.n_themes else '  '
        print(f"  {mark} #{t['idea_id']:>2} {t['theme'][:36]:<38} alloc={t['allocation_pct']:>5.2f}% / 매칭 {len(t['matched_tickers'])}종")

    top_themes = theme_list[:args.n_themes]
    rest_themes = theme_list[args.n_themes:]

    # 비례 재분배: 미선택 비중을 상위 N개에 비례 분배
    top_sum = sum(t['allocation_pct'] for t in top_themes)
    rest_sum = sum(t['allocation_pct'] for t in rest_themes)
    scale = (top_sum + rest_sum) / top_sum  # 90% 유지 (default 10% 제외분)
    print(f'\n  상위 {args.n_themes}개 합 {top_sum:.2f}% + 미선택 {rest_sum:.2f}% = {top_sum+rest_sum:.2f}%')
    print(f'  scale × {scale:.3f} → 상위 {args.n_themes}개 합 {top_sum*scale:.2f}%')

    for t in top_themes:
        t['allocation_pct_scaled'] = round(t['allocation_pct'] * scale, 3)

    print(f'\n[3/5] 테마별 종목 선정 (cap 초과 시 분할, MA60 위, 중복 회피)…')

    def combined_score(ticker):
        att = attr_map.get(ticker, 0)
        tsc = tech_scores.get(ticker, {}).get('tech_score')
        if tsc is None:
            return None
        return w_a * att + w_t * tsc

    intensity_w = {'direct': 1.0, 'indirect': 0.7, 'value_chain': 0.4, 'perception': 0.3}
    used_tickers = set()
    selected = []
    theme_shortfall = {}  # idea_id → 남은 비중 (종목 부족 시)

    for t in top_themes:
        # 테마 내 후보 점수화
        ranked = []
        for m in t['matched_tickers']:
            tkr = m['ticker']
            if tkr in exit_set or tkr in used_tickers:
                continue
            cb = combined_score(tkr)
            if cb is None:
                continue
            iw = intensity_w.get(m.get('intensity', 'indirect'), 0.7)
            tinfo = pf_meta.get(tkr, {})
            ranked.append({
                'ticker': tkr, 'name': m.get('name'),
                'intensity': m.get('intensity'),
                'combined': cb, 'weighted_score': cb * iw,
                'attractiveness': attr_map.get(tkr, 0),
                'tech_score': tech_scores.get(tkr, {}).get('tech_score'),
                'size_tier': tinfo.get('size_tier'),
                'mcap_eok': tinfo.get('mcap_eok'),
                'sector': tinfo.get('sector'),
            })
        ranked.sort(key=lambda x: -x['weighted_score'])

        # 종목 수 K 결정: target / avg_cap 올림. 최소 1, 최대 3 (의미있는 분산)
        import math
        target_alloc = t['allocation_pct_scaled']
        # 1위 종목의 cap 기준
        if not ranked:
            picked = []
        else:
            top_cap = size_cap_for(ranked[0]['size_tier'])
            K = max(1, min(3, math.ceil(target_alloc / top_cap)))
            # 후보 K개 점수 비례 분배
            cands = ranked[:K]
            score_sum = sum(c['weighted_score'] for c in cands)
            raw = {c['ticker']: target_alloc * c['weighted_score'] / score_sum for c in cands}
            # cap 보정 (반복)
            for _ in range(10):
                excess = 0
                for c in cands:
                    cap = size_cap_for(c['size_tier'])
                    if raw[c['ticker']] > cap:
                        excess += raw[c['ticker']] - cap
                        raw[c['ticker']] = cap
                if excess < 0.01:
                    break
                room_tickers = [c for c in cands if raw[c['ticker']] < size_cap_for(c['size_tier'])]
                if not room_tickers:
                    break  # 모든 종목 cap 도달 → 부족
                rsum = sum(c['weighted_score'] for c in room_tickers)
                for c in room_tickers:
                    add = excess * c['weighted_score'] / rsum
                    room = size_cap_for(c['size_tier']) - raw[c['ticker']]
                    raw[c['ticker']] += min(add, room)
            picked = [(c, raw[c['ticker']]) for c in cands if raw[c['ticker']] >= 0.5]
            # 최종 잔여
            actual_sum = sum(w for _, w in picked)
            remaining = target_alloc - actual_sum
            for c, _ in picked:
                used_tickers.add(c['ticker'])

        if not picked:
            print(f"  ❌ #{t['idea_id']:>2} {t['theme'][:30]:<32} → 후보 없음 (MA60 이탈/중복), 비중 {target_alloc:.2f}% → 처리 대기")
            theme_shortfall[t['idea_id']] = target_alloc
            continue

        log_line = f"  ⭐ #{t['idea_id']:>2} {t['theme'][:30]:<32} → {len(picked)}종 (목표 {target_alloc:.2f}%, K={K})"
        if remaining > 0.5:
            log_line += f"  ⚠ 잔여 {remaining:.2f}% 부족"
            theme_shortfall[t['idea_id']] = remaining
        print(log_line)

        for cand, w in picked:
            print(f"        {cand['ticker']} {cand['name'][:14]:<16} {w:>5.2f}%  ({cand['size_tier']}, 강도 {cand['intensity']}, 결합 {cand['combined']:.1f})")
            selected.append({
                'idea_id': t['idea_id'],
                'theme': t['theme'],
                'orig_allocation_pct': t['allocation_pct'],
                'theme_target_alloc': target_alloc,
                'allocation_pct': round(w, 3),
                'ticker': cand['ticker'],
                'name': cand['name'],
                'sector': cand['sector'],
                'mcap_eok': cand['mcap_eok'],
                'size_tier': cand['size_tier'],
                'size_cap_pct': size_cap_for(cand['size_tier']),
                'intensity': cand['intensity'],
                'attractiveness': cand['attractiveness'],
                'tech_score': cand['tech_score'],
                'combined_score': cand['combined'],
                'weighted_score': cand['weighted_score'],
                'tech_breakdown': tech_scores.get(cand['ticker'], {}).get('breakdown'),
                'indicators': tech_scores.get(cand['ticker'], {}).get('indicators'),
                'matched_ideas': pf_meta.get(cand['ticker'], {}).get('matched_ideas'),
            })

    # 부족분 처리
    total_shortfall = sum(theme_shortfall.values())
    if total_shortfall > 0.5:
        print(f'\n  부족분 합: {total_shortfall:.2f}%')
        if args.rebalance_on_shortfall and selected:
            # 다른 테마에 비례 재분배
            cur_sum = sum(s['allocation_pct'] for s in selected)
            for s in selected:
                add = total_shortfall * s['allocation_pct'] / cur_sum
                # cap 검사
                cap = size_cap_for(s['size_tier'])
                room = cap - s['allocation_pct']
                actual_add = min(add, room)
                s['allocation_pct'] = round(s['allocation_pct'] + actual_add, 3)
            print(f'  → 다른 테마에 비례 재분배 (cap 한도 내)')
        else:
            print(f'  → 현금 보유 (--rebalance-on-shortfall 없음)')

    print(f'\n[4/5] default_picks 4종 추가 (합 {default_alloc}%)…')
    n_default = len(default_list)
    per_default = default_alloc / n_default if n_default else 0
    for p in default_list:
        tkr = p['ticker']
        tinfo = pf_meta.get(tkr, {})
        selected.append({
            'idea_id': 'default',
            'theme': 'default_picks (매크로 무관)',
            'allocation_pct': round(per_default, 3),
            'ticker': tkr,
            'name': p['name'],
            'sector': tinfo.get('sector'),
            'mcap_eok': tinfo.get('mcap_eok'),
            'size_tier': tinfo.get('size_tier'),
            'intensity': None,
            'attractiveness': p.get('attractiveness'),
            'tech_score': tech_scores.get(tkr, {}).get('tech_score'),
            'combined_score': None,
            'macro_beta': p.get('macro_beta'),
            'catalyst': p.get('catalyst'),
            'tech_breakdown': tech_scores.get(tkr, {}).get('breakdown'),
            'indicators': tech_scores.get(tkr, {}).get('indicators'),
        })
        print(f"  default {tkr} {p['name']:<12} β={p.get('macro_beta'):.2f}  {per_default:.2f}%")

    # 자본대비
    equity_pct = pf['equity_pct']
    for s in selected:
        s['capital_weight_pct'] = round(s['allocation_pct'] * equity_pct / 100, 3)

    total_w = sum(s['allocation_pct'] for s in selected)

    print(f'\n[5/5] 문서 작성…')
    out = {
        'as_of': datetime.datetime.now().isoformat(),
        'method': f'테마 비중 골격 + 결합점수 최고 1종 (테마 {args.n_themes} + default {n_default})',
        'investor_type': pf['investor_type'],
        'equity_pct': equity_pct,
        'regime': pf['regime'],
        'n_themes_selected': args.n_themes,
        'n_default_picks': n_default,
        'n_holdings': len(selected),
        'total_weight_pct': round(total_w, 2),
        'selected_themes': [{'idea_id': t['idea_id'], 'theme': t['theme'],
                             'orig_alloc': t['allocation_pct'], 'scaled_alloc': t['allocation_pct_scaled']}
                            for t in top_themes],
        'excluded_themes': [{'idea_id': t['idea_id'], 'theme': t['theme'], 'alloc': t['allocation_pct']}
                            for t in rest_themes],
        'ma60_exit_excluded': sorted(exit_set),
        'holdings': selected,
    }
    out_json = OUT_JSON.replace('.json', f'{args.output_suffix}.json') if args.output_suffix else OUT_JSON
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # final_portfolio.json도 동일 내용 (호환성: entry_order_plan / daily_signals 가 사용)
    # holdings 구조를 final_portfolio 포맷에 맞춤
    holdings_compat = []
    for s in selected:
        holdings_compat.append({
            'ticker': s['ticker'],
            'name': s['name'],
            'sector': s['sector'],
            'weight_pct': s['allocation_pct'],
            'capital_weight_pct': s['capital_weight_pct'],
            'mcap_eok': s.get('mcap_eok'),
            'size_tier': s.get('size_tier'),
            'attractiveness': s['attractiveness'],
            'tech_score': s.get('tech_score'),
            'combined': s.get('combined_score'),
            'matched_ideas': [s['idea_id']] if s['idea_id'] != 'default' else [],
            'is_default_pick': s['idea_id'] == 'default',
            'macro_beta': s.get('macro_beta'),
            'theme': s.get('theme'),
            'intensity': s.get('intensity'),
            'tech_breakdown': s.get('tech_breakdown'),
            'indicators': s.get('indicators'),
        })
    final_compat = {
        'as_of': out['as_of'],
        'investor_type': out['investor_type'],
        'equity_pct': out['equity_pct'],
        'regime': out['regime'],
        'method': out['method'],
        'n_holdings': len(holdings_compat),
        'holdings': holdings_compat,
        'kospi_6m_pct': tech_data.get('kospi_6m_pct'),
        'model_portfolio_n': len(pf['holdings']),
        'excluded_ma60': out['ma60_exit_excluded'],
    }
    final_path = os.path.join(ROOT, '08_signals', 'final_portfolio.json')
    with open(final_path, 'w', encoding='utf-8') as f:
        json.dump(final_compat, f, ensure_ascii=False, indent=2)
    print(f'  + final_portfolio.json 갱신 (호환성)')

    L = []
    L.append(f'# 최종 포트폴리오 — 테마 비중 골격 유지 ({len(selected)}종목)')
    L.append('')
    L.append(f'**생성:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}')
    L.append(f'**투자자:** {pf["investor_type"]} (equity {equity_pct}%) | **레짐:** {pf["regime"]}')
    L.append(f'**구조:** 핵심 테마 {args.n_themes}개 (각 1종) + default_picks {n_default}종 = **{len(selected)}종목**')
    L.append(f'**비중 산식:** 테마 allocation 비례 재분배 (미선택 흡수) + default {default_alloc}%')
    L.append(f'**종목 선정:** 결합점수 = {w_a:.1f} × 매력도 + {w_t:.1f} × 기술점수, 매칭 강도(intensity) 가중')
    L.append('')
    L.append('## 1. 테마 비중 골격')
    L.append('')
    L.append('| 순위 | ID | 테마 | 원비중 | 재조정 | 선택 종목 | 매칭강도 | 매력 | 기술 | 결합 |')
    L.append('|---:|---:|---|---:|---:|---|---|---:|---:|---:|')
    theme_selected = [s for s in selected if s['idea_id'] != 'default']
    for i, s in enumerate(theme_selected, 1):
        L.append(f"| {i} | #{s['idea_id']} | {s['theme'][:32]} | {s.get('orig_allocation_pct',0):.2f}% | **{s['allocation_pct']:.2f}%** | {s['ticker']} {s['name']} | {s['intensity']} | {s['attractiveness']:.1f} | {s['tech_score']:.0f} | {s['combined_score']:.1f} |")
    L.append('')
    L.append('### 미선택 테마 (비중 재분배 대상)')
    L.append('')
    L.append('| ID | 테마 | 원비중 |')
    L.append('|---:|---|---:|')
    for t in rest_themes:
        L.append(f"| #{t['idea_id']} | {t['theme'][:34]} | {t['allocation_pct']:.2f}% |")
    L.append('')

    L.append('## 2. default_picks 4종 (매크로 무관, β<0.7)')
    L.append('')
    L.append('| 티커 | 종목명 | β | 매력도 | 비중 | catalyst |')
    L.append('|---|---|---:|---:|---:|---|')
    for s in selected:
        if s['idea_id'] == 'default':
            L.append(f"| {s['ticker']} | {s['name']} | {s.get('macro_beta',0):.2f} | {s['attractiveness']:.1f} | {s['allocation_pct']:.2f}% | {s.get('catalyst','-')} |")
    L.append('')

    L.append('## 3. 최종 보유 종목 (자본 1억 기준)')
    L.append('')
    L.append('| # | 티커 | 종목명 | 섹터 | 시총 | 테마 | 비중 | 자본대비 | 매력 | 기술 |')
    L.append('|---:|---|---|---|---|---|---:|---:|---:|---:|')
    for i, s in enumerate(selected, 1):
        theme_str = f"#{s['idea_id']}" if s['idea_id'] != 'default' else 'default'
        ts = s.get('tech_score')
        ts_str = f"{ts:.0f}" if ts is not None else '-'
        L.append(f"| {i} | {s['ticker']} | {s['name']} | {s['sector'] or '-'} | {s.get('size_tier','-')} | {theme_str} | **{s['allocation_pct']:.2f}%** | {s['capital_weight_pct']:.2f}% | {s['attractiveness']:.1f} | {ts_str} |")
    L.append('')
    L.append(f'**합계: 비중 {total_w:.2f}% / 자본대비 {sum(s["capital_weight_pct"] for s in selected):.2f}%**')
    L.append('')

    L.append('## 4. 핵심 지표 (기술분석)')
    L.append('')
    L.append('| 티커 | 종가(원) | MA60 | 이격% | 6M수익률 | RSI | 52w위치 | σ연환산 |')
    L.append('|---|---:|---:|---:|---:|---:|---:|---:|')
    for s in selected:
        ind = s.get('indicators') or {}
        L.append(f"| {s['ticker']} | {ind.get('close',0):,.0f} | {ind.get('ma60','-')} | {ind.get('ma60_margin_pct','-')}% | {ind.get('ret_6m_pct','-')}% | {ind.get('rsi14','-')} | {ind.get('pos_52w_pct','-')}% | {ind.get('sigma_annual_pct','-')}% |")
    L.append('')

    L.append('## 5. MA60 이탈 자동 제외')
    L.append('')
    for t in sorted(exit_set):
        tinfo = tech_scores.get(t, {})
        ind = tinfo.get('indicators') or {}
        L.append(f"- {t} {tinfo.get('name','?')}: 종가 {ind.get('close','-')} < MA60 {ind.get('ma60','-')}")
    L.append('')

    L.append('## 6. 운영 가이드')
    L.append('')
    L.append('- **테마 비중 골격**은 4단계 산출물 기반. 매크로/레짐 변화 시 갱신')
    L.append('- **매일 종가 후**: `compute_daily_signals.py` → 진입/추가/청산 시그널')
    L.append('- **주 1회 (금요일)**: `build_theme_portfolio.py` 재실행 → 테마 내 최고 종목 변경 여부 검토')
    L.append('- **테마 종목 청산 시**: 같은 테마 내 차순위 종목으로 자동 대체 또는 다음 사이클까지 현금 보유')
    L.append('- **분할 매수**: 1차 50% / 2차 25% (-5%, MA60 위) / 3차 25% (-10%, MA60 위)')
    L.append('- **청산**: MA60 하향 / 트레일링 진입가 ×0.85')
    L.append('')
    L.append('---')
    L.append('*면책: 본 자료는 정보 제공 목적의 분석 결과이며 투자 권유가 아닙니다.*')

    out_md = OUT_MD.replace('.md', f'{args.output_suffix}.md') if args.output_suffix else OUT_MD
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print(f'  저장: {OUT_JSON}')
    print(f'         {OUT_MD}')
    print()
    print(f'=== 테마 비중 골격 포트 ({len(selected)}종목) ===')
    for i, s in enumerate(selected, 1):
        theme_str = f"#{s['idea_id']}" if s['idea_id'] != 'default' else 'default'
        print(f"  {i:>2} {s['ticker']} {s['name'][:14]:<16} {theme_str:<8} 비중 {s['allocation_pct']:>5.2f}%  자본 {s['capital_weight_pct']:>5.2f}%")


if __name__ == '__main__':
    main()
