"""자동매매 시뮬레이션 — 시스템 시그널 그대로 추종 시 수익률 측정

탭 구성:
  1) 백테스트 (과거 24M walk-forward 결과)
  2) 라이브 시뮬레이션 (오늘부터 forward, 시스템 자동 추천)
"""
import streamlit as st
import pandas as pd
import datetime, os, sys, json, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader, db, perf, costs, auth

st.set_page_config(page_title='자동매매 시뮬레이션', page_icon='🤖', layout='wide')
auth.require_login()
auth.logout_button()
st.title('🤖 자동매매 시뮬레이션 — 시스템 추천대로 매매 시 수익률')

st.info("""
**목적:** 사용자가 시스템 시그널을 그대로 따라 매매했을 때 누적 수익률 추적.
**구분:** 사용자 수동 매매 (`📝 포트폴리오 입력`)와 **분리**하여 별도 DB 저장.
""")

tab_live, tab_bt = st.tabs(['🤖 라이브 시뮬레이션 (오늘부터 Forward)', '📊 과거 백테스트 (12M & 24M)'])

# ════════════════════════════════════════════════════════════════
# 탭 1: 과거 백테스트 (12M & 24M)
# ════════════════════════════════════════════════════════════════
with tab_bt:
    st.subheader('📊 과거 백테스트 결과 — 12M & 24M')
    st.caption('1단계(투자성향)~8단계(시그널) 통합. v8 (분할 75/15/10 + MA20 청산 + KOSPI 강세장 필터) 채택.')
    st.caption('💡 매매비용 반영 — 키움 수수료 0.015% × 2 + 거래세 0.18% + 슬리피지 0.05% × 2 (왕복 약 0.28%) | 신용이자 연 6%')

    bt = loader.load('backtest')
    if not bt:
        st.warning('백테스트 결과 없음. `_workspace/validation/backtest_full_pipeline.py --weeks 104` 실행 필요.')
    else:
        ret12 = bt.get('return_12m_pct')
        kospi12 = bt.get('kospi_12m_pct')
        excess12 = bt.get('excess_12m_pct')
        ret24 = bt.get('return_24m_pct')
        kospi24 = bt.get('kospi_24m_pct')
        excess24 = bt.get('excess_24m_pct')
        sharpe = bt.get('sharpe_weekly_ann')
        mdd = bt.get('mdd_pct')
        period_start = bt.get('period_start', '-')
        period_end = bt.get('period_end', '-')
        initial_cap = bt.get('initial_capital', 0)
        final_v = bt.get('final_value', 0)
        n_weeks = bt.get('n_weeks', '-')
        n_trades = bt.get('n_trades', '-')

        # 12M & 24M 나란히
        c12, c24 = st.columns(2)
        with c12:
            st.markdown('### 📅 12개월 (1년)')
            col1, col2 = st.columns(2)
            col1.metric('전략 수익률', f"{ret12:.2f}%" if ret12 is not None else '-')
            col2.metric('KOSPI 동기간', f"{kospi12:.2f}%" if kospi12 is not None else '-')
            col3, col4 = st.columns(2)
            col3.metric('초과 (vs KOSPI)', f"{excess12:+.2f}%p" if excess12 is not None else '-')
            col4.metric('연환산', f"{ret12:.2f}%" if ret12 is not None else '-')
        with c24:
            st.markdown('### 📅 24개월 (2년)')
            col1, col2 = st.columns(2)
            col1.metric('전략 수익률', f"{ret24:.2f}%" if ret24 is not None else '-')
            col2.metric('KOSPI 동기간', f"{kospi24:.2f}%" if kospi24 is not None else '-')
            col3, col4 = st.columns(2)
            col3.metric('초과 (vs KOSPI)', f"{excess24:+.2f}%p" if excess24 is not None else '-')
            ann24 = ((1 + ret24/100) ** 0.5 - 1) * 100 if ret24 is not None else None
            col4.metric('연환산', f"{ann24:.2f}%" if ann24 is not None else '-')

        st.divider()
        st.markdown('### 위험·효율 지표 (24M 기준)')
        col1, col2, col3, col4 = st.columns(4)
        col1.metric('샤프 (주별 ann)', f"{sharpe}" if sharpe else '-')
        col2.metric('MDD', f"{mdd}%" if mdd is not None else '-')
        col3.metric('매매 횟수', n_trades)
        col4.metric('백테스트 주 수', f"{n_weeks}주")

        col5, col6, col7 = st.columns(3)
        col5.metric('기간', f"{period_start} ~ {period_end}")
        col6.metric('시작 자본', f'{initial_cap:,}원')
        col7.metric('최종 평가', f'{final_v:,.0f}원')

        # 일별 평가 시계열
        weekly = bt.get('weekly_log_sample')
        if weekly and isinstance(weekly, list) and len(weekly) > 1:
            st.markdown('### 주별 누적 수익률 시계열 (sample)')
            df = pd.DataFrame(weekly)
            df['date_dt'] = pd.to_datetime(df['date'], format='%Y%m%d', errors='coerce')
            df = df.sort_values('date_dt').set_index('date_dt')
            # 가능한 수익률 컬럼 찾기
            y_candidates = ['cum_return_pct', 'return_pct', 'total_value', 'portfolio_value', 'equity']
            y_cols = [c for c in y_candidates if c in df.columns]
            if y_cols:
                st.line_chart(df[y_cols])
            else:
                # 사용 가능 컬럼 표시
                st.dataframe(df.head(20), use_container_width=True)
                st.caption(f'표시 가능한 컬럼: {list(df.columns)}')

        # 매크로 레짐 분포
        regime_dist = bt.get('macro_regime_distribution')
        if regime_dist:
            st.markdown('### 매크로 레짐 분포 (24M)')
            rdf = pd.DataFrame(list(regime_dist.items()), columns=['레짐', '주 수'])
            rdf['비율(%)'] = rdf['주 수'] / rdf['주 수'].sum() * 100
            st.dataframe(rdf, hide_index=True, use_container_width=True)

        # 매매 sample
        trades_sample = bt.get('trades_sample')
        if trades_sample:
            with st.expander(f'📜 매매 sample ({len(trades_sample)}건)'):
                st.dataframe(pd.DataFrame(trades_sample), hide_index=True, use_container_width=True)

        # v6 / v7 / v8 비교
        st.divider()
        st.subheader('📊 청산 로직 버전 비교 (24M)')
        st.caption('v6 = MA60 only · v7 = MA20 50% + MA60 잔여 · **v8 = v7 + KOSPI 강세장 필터 (채택)**')
        bt_v6 = loader.load('backtest_v6')
        bt_v7 = loader.load('backtest_v7')
        bt_v8 = loader.load('backtest_v8')
        comp_rows = []
        for name, data in [('v6 (MA60 only)', bt_v6), ('v7 (MA20+MA60)', bt_v7), ('v8 (강세장 필터) ⭐', bt_v8)]:
            if not data:
                continue
            comp_rows.append({
                '버전': name,
                '24M 수익률': data.get('return_24m_pct'),
                '24M KOSPI': data.get('kospi_24m_pct'),
                '24M 초과(p)': data.get('excess_24m_pct'),
                '12M 수익률': data.get('return_12m_pct'),
                '샤프': data.get('sharpe_weekly_ann'),
                'MDD(%)': data.get('mdd_pct'),
                '매매': data.get('n_trades'),
            })
        if comp_rows:
            comp_df = pd.DataFrame(comp_rows)
            st.dataframe(comp_df, hide_index=True, use_container_width=True,
                         column_config={
                             '24M 수익률': st.column_config.NumberColumn(format='%+.2f%%'),
                             '24M KOSPI': st.column_config.NumberColumn(format='%+.2f%%'),
                             '24M 초과(p)': st.column_config.NumberColumn(format='%+.2f'),
                             '12M 수익률': st.column_config.NumberColumn(format='%+.2f%%'),
                             '샤프': st.column_config.NumberColumn(format='%.2f'),
                             'MDD(%)': st.column_config.NumberColumn(format='%+.2f'),
                         })

        with st.expander('상세 JSON'):
            st.json(bt, expanded=False)


# ════════════════════════════════════════════════════════════════
# 탭 2: 라이브 시뮬레이션 (Forward-test)
# ════════════════════════════════════════════════════════════════
with tab_live:
    st.subheader('🤖 라이브 시뮬레이션 — 시스템 자동 추천 추종')

    sims = db.list_simulations()

    col_a, col_b = st.columns([1, 1])

    # ─── 신규 시뮬레이션 생성 (투자성향 유형 자동 매핑) ─────────
    with col_a:
        st.markdown('### ➕ 새 시뮬레이션 시작')

        # 현재 진단된 투자성향 (constraints.json) 로드
        WS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cur_profile_path = os.path.join(WS_ROOT, '01_profile', 'constraints.json')
        cur_profile_type = None
        if os.path.exists(cur_profile_path):
            try:
                cur_profile_type = json.load(open(cur_profile_path, encoding='utf-8')).get('investor_type')
            except Exception:
                pass

        # 5개 유형별 theme_portfolio 존재 확인
        AVAILABLE_PROFILES = []
        for pname in ['안정형', '안정추구형', '위험중립형', '적극투자형', '공격투자형']:
            tp_path = os.path.join(WS_ROOT, '08_signals', f'theme_portfolio_{pname}.json')
            if os.path.exists(tp_path):
                AVAILABLE_PROFILES.append(pname)

        if cur_profile_type:
            st.info(f'👤 현재 진단된 투자성향: **{cur_profile_type}**')

        with st.form('new_sim'):
            # 사용할 포트 선택 (진단 결과 default)
            portfolio_choices = ['현재 모델 포트 (theme_portfolio.json)'] + [f'유형: {p}' for p in AVAILABLE_PROFILES]
            default_idx = 0
            if cur_profile_type and cur_profile_type in AVAILABLE_PROFILES:
                default_idx = AVAILABLE_PROFILES.index(cur_profile_type) + 1
            portfolio_choice = st.selectbox(
                '📦 사용할 포트폴리오',
                options=portfolio_choices,
                index=default_idx,
                help='투자성향 진단 결과에 따라 자동 선택됩니다. 다른 유형도 시뮬레이션 가능.'
            )

            sim_name = st.text_input('시뮬레이션 이름',
                                     value=f'{cur_profile_type or "default"} {datetime.date.today().strftime("%y%m%d")}')
            start_date = st.date_input('시작 일자', value=datetime.date.today())
            start_capital_eok = st.number_input('시작 자본 (억원)', min_value=0.1, value=1.0, step=0.1)
            notes = st.text_area('메모', value='시스템 자동 추천 포트폴리오 추종', height=80)
            submitted = st.form_submit_button('🚀 시뮬레이션 시작', type='primary', use_container_width=True)

            if submitted:
                # 선택한 포트 → theme_portfolio 또는 entry_plan 로드
                if portfolio_choice.startswith('유형:'):
                    pname = portfolio_choice.split('유형:')[1].strip()
                    tp_path = os.path.join(WS_ROOT, '08_signals', f'theme_portfolio_{pname}.json')
                    tp = json.load(open(tp_path, encoding='utf-8'))
                    holdings = tp['holdings']
                    cap_won = start_capital_eok * 1e8
                    # v19: cap_pct 합이 equity_pct를 초과하면 비례 정규화 (한도 초과 매수 방지)
                    cap_total = sum(h.get('capital_weight_pct', 0) for h in holdings)
                    equity_pct = tp.get('equity_pct', 100)
                    cap_scale = 1.0
                    if cap_total > equity_pct:
                        cap_scale = equity_pct / cap_total
                    sim_id = db.create_simulation(
                        name=sim_name,
                        start_date=start_date.strftime('%Y%m%d'),
                        start_capital=cap_won,
                        notes=f'{notes} | 유형: {pname} (equity {equity_pct}%) | cap_scale={cap_scale:.3f}',
                    )
                    n_orders = 0
                    for h in holdings:
                        ind = h.get('indicators') or {}
                        price = ind.get('close')
                        if not price:
                            continue
                        cap_pct = h.get('capital_weight_pct', 0) * cap_scale  # equity 한도 내 정규화
                        budget = cap_won * cap_pct / 100
                        first_won = budget * 0.75  # v8: 1차 75%
                        shares = int(first_won / price) if price > 0 else 0
                        if shares <= 0 and budget >= price:
                            shares = 1
                        if shares <= 0:
                            continue
                        c = costs.calc_trade_cost(price, shares, 'BUY')
                        theme_id = 'default' if h['idea_id'] == 'default' else f'#{h["idea_id"]}'
                        db.add_sim_trade(
                            sim_id=sim_id,
                            trade_date=start_date.strftime('%Y%m%d'),
                            ticker=h['ticker'], name=h['name'],
                            action='BUY', shares=shares, price=price,
                            fee=c['fee'] + c['slippage'], tax=c['tax'],
                            theme_id=theme_id,
                            signal_type='ENTRY_1ST',
                            note=f'{pname} 1차 75% (비중 {cap_pct:.2f}%)',
                        )
                        n_orders += 1
                    st.success(f'✅ **{pname}** 유형 시뮬레이션 #{sim_id} 시작! 1차 매수 {n_orders}건')
                    st.rerun()
                else:
                    # 기본 entry_order_plan.json
                    entry_plan = loader.load('entry_plan')
                    if not entry_plan or not entry_plan.get('orders_1st'):
                        st.error('entry_order_plan.json 없음.')
                    else:
                        sim_id = db.create_simulation(
                            name=sim_name,
                            start_date=start_date.strftime('%Y%m%d'),
                            start_capital=start_capital_eok * 1e8,
                            notes=notes,
                        )
                        n_orders = 0
                        for o in entry_plan['orders_1st']:
                            if o.get('first_shares', 0) > 0:
                                mi = o.get('matched_ideas') or []
                                theme_id = 'default' if o.get('is_default_pick') else (f'#{mi[0]}' if mi else None)
                                price = float(o['close'])
                                shares = int(o['first_shares'])
                                c = costs.calc_trade_cost(price, shares, 'BUY')
                                db.add_sim_trade(
                                    sim_id=sim_id,
                                    trade_date=start_date.strftime('%Y%m%d'),
                                    ticker=o['ticker'], name=o['name'],
                                    action='BUY', shares=shares, price=price,
                                    fee=c['fee'] + c['slippage'], tax=c['tax'],
                                    theme_id=theme_id,
                                    signal_type='ENTRY_1ST',
                                    note=f'1차 매수 (비중 {o["capital_weight_pct"]:.2f}%) | 비용 {c["fee"]+c["slippage"]:,}원',
                                )
                                n_orders += 1
                        st.success(f'✅ 시뮬레이션 #{sim_id} 시작! 1차 매수 {n_orders}건')
                        st.rerun()

    # ─── 진행 중 시뮬레이션 목록 ─────────────────────────
    with col_b:
        st.markdown('### 📋 진행 중인 시뮬레이션')
        if not sims:
            st.info('진행 중인 시뮬레이션이 없습니다. 왼쪽에서 새로 시작하세요.')
        else:
            sim_options = {f"#{s['id']} {s['name']} ({s['start_date']}, {s['status']})": s['id'] for s in sims}
            selected = st.selectbox('시뮬레이션 선택', options=list(sim_options.keys()))
            sim_id = sim_options[selected]
            sim = db.get_simulation(sim_id)
            st.json({
                '시작 일자': sim['start_date'],
                '시작 자본': f"{sim['start_capital']/1e8:.2f}억원",
                '상태': sim['status'],
                '마지막 동기화': sim['last_synced'],
                '메모': sim['notes'],
            })
            col_x, col_y = st.columns(2)
            with col_x:
                if st.button('🗑 삭제', type='secondary'):
                    db.delete_simulation(sim_id)
                    st.warning(f'시뮬레이션 #{sim_id} 삭제')
                    st.rerun()
            with col_y:
                if st.button('🔄 오늘 시그널 적용', type='primary'):
                    daily = loader.load('daily_signals')
                    if not daily:
                        st.error('daily_signals.json 없음')
                    else:
                        today = daily.get('as_of', datetime.date.today().strftime('%Y%m%d'))
                        # 이미 같은 날 처리됐는지 확인
                        existing = [t for t in db.list_sim_trades(sim_id) if t['trade_date'] == today]
                        if existing:
                            st.warning(f'{today} 이미 적용됨 ({len(existing)}건). 중복 적용 방지.')
                        else:
                            # v19: 시뮬 universe lock — 시뮬 시작 시 매수한 종목만 시그널 적용
                            sim_universe = set(
                                t['ticker'] for t in db.list_sim_trades(sim_id)
                                if t['action'] == 'BUY'
                            )
                            applied = 0
                            skipped_universe = 0
                            for s in daily.get('signals', []):
                                if s['signal'] == 'WATCH':
                                    continue
                                # 시뮬 portfolio 외 종목 차단 (다른 유형 시그널 침범 방지)
                                if s['ticker'] not in sim_universe:
                                    skipped_universe += 1
                                    continue
                                if s['signal'] in ('EXIT_MA60_FULL', 'EXIT_MA60_REMAINDER', 'EXIT_MA20_PARTIAL', 'EXIT_MA60', 'EXIT_TRAILING'):
                                    pos = db.get_sim_positions(sim_id).get(s['ticker'])
                                    if pos:
                                        # MA20 부분 청산은 50%, 나머지는 전량
                                        sell_ratio = 0.5 if s['signal'] == 'EXIT_MA20_PARTIAL' else 1.0
                                        sell_shares = int(pos['shares'] * sell_ratio)
                                        price = s.get('close', 0)
                                        c = costs.calc_trade_cost(price, sell_shares, 'SELL')
                                        db.add_sim_trade(
                                            sim_id=sim_id, trade_date=today,
                                            ticker=s['ticker'], name=s['name'],
                                            action='SELL', shares=sell_shares,
                                            price=price,
                                            fee=c['fee'] + c['slippage'], tax=c['tax'],
                                            theme_id=pos.get('theme_id'),
                                            signal_type=s['signal'],
                                            note=(s.get('reason', '') + f' | 비용 {c["total"]:,}원')[:240],
                                        )
                                        applied += 1
                                elif s['signal'] == 'ENTRY_1ST':
                                    # v19: 이미 보유 중인 종목이면 ENTRY_1ST 중복 차단 (시뮬 시작 시 이미 1차 매수)
                                    if db.get_sim_positions(sim_id).get(s['ticker']):
                                        continue
                                    cap_pct = s.get('capital_weight_pct', 0)
                                    budget = sim['start_capital'] * cap_pct / 100
                                    first_won = budget * 0.75  # v8: 1차 75%
                                    price = s.get('close', 0)
                                    shares = int(first_won / price) if price > 0 else 0
                                    if shares > 0:
                                        c = costs.calc_trade_cost(price, shares, 'BUY')
                                        db.add_sim_trade(
                                            sim_id=sim_id, trade_date=today,
                                            ticker=s['ticker'], name=s['name'],
                                            action='BUY', shares=shares,
                                            price=price,
                                            fee=c['fee'] + c['slippage'], tax=0,
                                            theme_id=None,
                                            signal_type='ENTRY_1ST',
                                            note=(s.get('reason', '') + f' | 비용 {c["total"]:,}원')[:240],
                                        )
                                        applied += 1
                            db.update_simulation(sim_id, last_synced=today)
                            msg = f'✅ {today} 시그널 {applied}건 적용 (키움 매매비용 자동 반영)'
                            if skipped_universe > 0:
                                msg += f' | 🛡 시뮬 universe 외 {skipped_universe}건 차단 (다른 유형 portfolio 종목)'
                            st.success(msg)
                            st.rerun()

    st.divider()

    # ─── 선택된 시뮬레이션 상세 ─────────────────────────
    if sims:
        st.subheader(f'📈 시뮬레이션 #{sim_id} 성과')
        sim_positions = db.get_sim_positions(sim_id)
        if not sim_positions:
            st.info('보유 종목 없음')
        else:
            # 가격 갱신 (캐시)
            @st.cache_data(ttl=600, show_spinner='📡 KRX 가격 수집…')
            def fetch_sim_data(tickers_tuple: tuple, days: int):
                tickers = list(tickers_tuple)
                price_history = perf.fetch_close_prices(tickers, days=days)
                kospi = perf.fetch_kospi_history(days=days)
                return price_history, kospi

            days = 130
            tickers = tuple(sorted(sim_positions.keys()))
            price_history, kospi = fetch_sim_data(tickers, days)
            latest_prices = {t: ph[max(ph.keys())] for t, ph in price_history.items() if ph}

            evals = perf.evaluate_positions(sim_positions, latest_prices)
            if not evals:
                st.warning('⚠️ 가격 데이터를 일시적으로 가져오지 못했습니다(yfinance 미응답/레이트리밋). '
                           '잠시 후 새로고침하세요. 보유 종목 자체는 정상 저장되어 있습니다.')
                st.stop()
            total_cost = sum(p['cost'] for p in evals)
            total_mkt = sum(p['market_value'] for p in evals)
            total_pnl = total_mkt - total_cost
            # v19: 시작 자본 분모로 통일 (아래 시계열과 일치)
            total_pnl_pct_cost = (total_mkt / total_cost - 1) * 100 if total_cost > 0 else 0
            total_pnl_pct_cap = total_pnl / sim['start_capital'] * 100 if sim['start_capital'] > 0 else 0

            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric('시작 자본', f"{sim['start_capital']/1e8:.2f}억")
            col2.metric('투입 금액', f'{total_cost:,.0f}원',
                          help=f"투입률 {total_cost/sim['start_capital']*100:.1f}% (자본 대비)")
            col3.metric('현재 평가', f'{total_mkt:,.0f}원')
            col4.metric('수익률 (자본 대비)', f'{total_pnl:,.0f}원', f'{total_pnl_pct_cap:+.2f}%',
                          help='시작 자본 분모. 아래 시계열·KOSPI 비교와 일치.')
            col5.metric('수익률 (매수원가 대비)', f'{total_pnl_pct_cost:+.2f}%',
                          help='매수 cost_basis 분모. 보유 종목 표의 종목별 수익률(%)과 일관.')

            # 보유 종목 평가
            st.markdown('### 시뮬레이션 보유 종목')
            edf = pd.DataFrame(evals)
            display_cols = ['ticker', 'name', 'shares', 'avg_price', 'cur_price', 'cost', 'market_value', 'pnl', 'pnl_pct', 'theme_id']
            display_df = edf[display_cols].rename(columns={
                'ticker': '티커', 'name': '종목명', 'shares': '수량',
                'avg_price': '평균단가', 'cur_price': '현재가',
                'cost': '매수금액', 'market_value': '평가금액',
                'pnl': '평가손익', 'pnl_pct': '수익률(%)', 'theme_id': '테마',
            })
            st.dataframe(display_df, hide_index=True, use_container_width=True,
                         column_config={
                             '평균단가': st.column_config.NumberColumn(format='%.0f원'),
                             '현재가': st.column_config.NumberColumn(format='%.0f원'),
                             '매수금액': st.column_config.NumberColumn(format='%d원'),
                             '평가금액': st.column_config.NumberColumn(format='%d원'),
                             '평가손익': st.column_config.NumberColumn(format='%d원'),
                             '수익률(%)': st.column_config.NumberColumn(format='%+.2f%%'),
                         })

            # 일별 평가 시계열
            st.divider()
            st.markdown('### 누적 수익률 시계열')
            all_dates = set()
            for ph in price_history.values():
                all_dates.update(ph.keys())
            all_dates = sorted(all_dates)
            first_buy = min(p.get('first_buy_date') for p in sim_positions.values() if p.get('first_buy_date'))
            eval_dates = [d for d in all_dates if d >= first_buy]

            if eval_dates:
                sim_trades = db.list_sim_trades(sim_id)
                base_cap = sim['start_capital']

                # 투자 유형 명시적 선택 (자동 추론 X — 사용자 혼란 방지)
                PROFILE_EQUITY = {
                    '안정형 (equity 60% · 신용 X)': 60,
                    '안정추구형 (equity 80% · 신용 X)': 80,
                    '위험중립형 (equity 100% · 신용 X)': 100,
                    '적극투자형 (equity 125% · 신용 25%)': 125,
                    '공격투자형 (equity 150% · 신용 50%)': 150,
                }
                default_idx = 2  # 위험중립형
                sim_meta = (sim.get('notes') or '') + ' ' + (sim.get('name') or '')
                for i, key in enumerate(PROFILE_EQUITY.keys()):
                    profile_name = key.split(' ')[0]
                    if profile_name in sim_meta:
                        default_idx = i
                        break

                profile_choice = st.selectbox(
                    '🎯 시뮬레이션 투자 유형 (equity % · 신용 사용 여부)',
                    options=list(PROFILE_EQUITY.keys()),
                    index=default_idx,
                    help='equity 100% 이하 = 신용 미사용 (신용이자 0원). 125%+ = 신용 사용분에 연 6% 이자.',
                )
                sim_equity_pct = PROFILE_EQUITY[profile_choice]

                daily_v = perf.daily_portfolio_value(sim_trades, price_history, eval_dates,
                                                      credit_interest_pct=costs.CREDIT_INTEREST_PCT_ANNUAL,
                                                      base_capital=base_cap,
                                                      equity_pct=sim_equity_pct)
                # v19: 시작 자본 대비 수익률 (시그널 적용 후 분모 폭증 방지)
                metrics = perf.calc_perf_metrics(daily_v, kospi, base_capital=base_cap)
                last_v = daily_v[max(daily_v.keys())]
                if metrics:
                    mc1, mc2, mc3, mc4 = st.columns(4)
                    mc1.metric('누적 수익률 (Net, 비용·세금·이자 반영)',
                               f"{metrics['cum_return_pct']:+.2f}%")
                    if metrics.get('kospi_return_pct') is not None:
                        mc2.metric('KOSPI 동기간', f"{metrics['kospi_return_pct']:+.2f}%")
                        mc3.metric('초과', f"{metrics['excess_vs_kospi_pct']:+.2f}%p")
                    if metrics.get('sharpe_annual') is not None:
                        mc4.metric('샤프', f"{metrics['sharpe_annual']}")

                # 비용 내역 — 신용 사용 안 하면 신용이자 컬럼 숨김
                trade_costs = sum((t.get('fee') or 0) + (t.get('tax') or 0) for t in sim_trades)
                if sim_equity_pct > 100:
                    cost_cols = st.columns(3)
                    cost_cols[0].metric('💰 누적 배당', f"{last_v.get('dividend_cum', 0):,.0f}원")
                    cost_cols[1].metric('💸 누적 신용이자',
                                          f"{last_v.get('credit_interest_cum', 0):,.0f}원",
                                          help=f'신용 사용액: 자본의 {sim_equity_pct - 100:.0f}% × 연 6%')
                    cost_cols[2].metric('🧾 매매비용 합 (수수료+세금)', f"{trade_costs:,.0f}원")
                else:
                    cost_cols = st.columns(2)
                    cost_cols[0].metric('💰 누적 배당', f"{last_v.get('dividend_cum', 0):,.0f}원")
                    cost_cols[1].metric('🧾 매매비용 합 (수수료+세금)', f"{trade_costs:,.0f}원")
                    st.caption(f'ℹ️ {profile_choice.split(" ")[0]} → 신용 미사용 (신용이자 0원)')

                # ─── 누적 수익률 시계열 비교 차트 (시뮬 vs KOSPI vs 초과) ─────
                st.markdown(f'### 📈 누적 수익률 시계열 ({sim["start_date"]} ~ 현재)')

                df_daily = pd.DataFrame([
                    {'date': d, 'total_value': v['total_value'], 'cost_basis': v['cost_basis']}
                    for d, v in daily_v.items()
                ])
                df_daily['date_dt'] = pd.to_datetime(df_daily['date'])
                df_daily = df_daily.sort_values('date_dt').reset_index(drop=True)
                # v19: PnL / 시작 자본 (metric과 일치)
                df_daily['시뮬레이션 수익률(%)'] = (df_daily['total_value'] - df_daily['cost_basis']) / base_cap * 100

                # KOSPI 같은 날짜로 정렬 + 시작 시점 정규화
                kospi_df = None
                if kospi:
                    kdf = pd.DataFrame([{'date_dt': pd.to_datetime(d), 'close': v} for d, v in kospi.items()])
                    kdf = kdf.sort_values('date_dt').reset_index(drop=True)
                    asof_first = df_daily.iloc[0]['date_dt']
                    kdf_filtered = kdf[kdf['date_dt'] >= asof_first].copy()
                    if not kdf_filtered.empty:
                        first_k = kdf_filtered.iloc[0]['close']
                        kdf_filtered['KOSPI 수익률(%)'] = (kdf_filtered['close'] / first_k - 1) * 100
                        kospi_df = kdf_filtered[['date_dt', 'KOSPI 수익률(%)']]

                # Plotly 차트 (시뮬 + KOSPI + 초과 영역)
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_daily['date_dt'], y=df_daily['시뮬레이션 수익률(%)'],
                        mode='lines', name='시뮬레이션 (Net)',
                        line=dict(color='#0050b3', width=2.5),
                    ))
                    if kospi_df is not None:
                        fig.add_trace(go.Scatter(
                            x=kospi_df['date_dt'], y=kospi_df['KOSPI 수익률(%)'],
                            mode='lines', name='KOSPI',
                            line=dict(color='#999', width=2, dash='dash'),
                        ))
                        # 초과 수익률 영역 (시뮬 - KOSPI)
                        merged = pd.merge_asof(
                            df_daily[['date_dt', '시뮬레이션 수익률(%)']].sort_values('date_dt'),
                            kospi_df.sort_values('date_dt'),
                            on='date_dt', direction='backward'
                        )
                        merged['초과(%p)'] = merged['시뮬레이션 수익률(%)'] - merged['KOSPI 수익률(%)']
                        fig.add_trace(go.Scatter(
                            x=merged['date_dt'], y=merged['초과(%p)'],
                            mode='lines', name='초과 수익률 (%p)',
                            line=dict(color='#52c41a', width=1.5),
                            fill='tozeroy', fillcolor='rgba(82,196,26,0.15)',
                        ))
                    # 0% 기준선
                    fig.add_hline(y=0, line_dash='dot', line_color='#ccc')
                    fig.update_layout(
                        height=450,
                        hovermode='x unified',
                        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                        xaxis_title='', yaxis_title='수익률 (%)',
                        margin=dict(l=10, r=10, t=10, b=10),
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # 최종 outperform 지표 강조
                    if kospi_df is not None and not merged.empty:
                        final_excess = merged['초과(%p)'].iloc[-1]
                        st.markdown(
                            f"### 🎯 **vs KOSPI 최종 초과 수익: "
                            f"<span style='color:{'#52c41a' if final_excess > 0 else '#f5222d'}; font-size:1.5em'>"
                            f"{final_excess:+.2f}%p</span>**",
                            unsafe_allow_html=True
                        )
                        # 일별 outperform 비율
                        outperform_days = (merged['초과(%p)'] > 0).sum()
                        total_days = len(merged)
                        if total_days > 0:
                            st.caption(
                                f'📊 outperform 일수: {outperform_days}/{total_days}일 ({outperform_days/total_days*100:.1f}%) | '
                                f'최대 초과 {merged["초과(%p)"].max():+.2f}%p | '
                                f'최소 초과 {merged["초과(%p)"].min():+.2f}%p'
                            )
                except ImportError:
                    # plotly 없으면 기존 line_chart
                    chart_df = df_daily[['date_dt', '시뮬레이션 수익률(%)']].set_index('date_dt')
                    if kospi_df is not None:
                        chart_df = chart_df.join(kospi_df.set_index('date_dt'), how='outer').ffill()
                    st.line_chart(chart_df)

            # 시뮬레이션 매매 이력
            st.divider()
            with st.expander(f'📜 매매 이력 ({len(db.list_sim_trades(sim_id))}건)'):
                sim_trades = db.list_sim_trades(sim_id)
                if sim_trades:
                    tdf = pd.DataFrame(sim_trades)
                    tdf['금액'] = (tdf['shares'] * tdf['price']).astype(int)
                    st.dataframe(tdf[['trade_date', 'ticker', 'name', 'action', 'shares', 'price', '금액', 'signal_type', 'theme_id', 'note']],
                                 hide_index=True, use_container_width=True)

    st.divider()
    st.markdown("""
**📌 운영 방법**
1. 시뮬레이션 시작 시 현재 `entry_order_plan.json`의 1차 매수가 자동 등록
2. 매일 종가 후 `compute_daily_signals.py` 실행 → 시그널 생성
3. "🔄 오늘 시그널 적용" 버튼으로 시뮬레이션 매매에 시그널 반영
4. 누적 수익률·KOSPI 대비 자동 갱신
""")
