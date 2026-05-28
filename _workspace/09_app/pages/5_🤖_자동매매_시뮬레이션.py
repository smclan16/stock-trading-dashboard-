"""자동매매 시뮬레이션 — 시스템 시그널 그대로 추종 시 수익률 측정

탭 구성:
  1) 백테스트 (과거 24M walk-forward 결과)
  2) 라이브 시뮬레이션 (오늘부터 forward, 시스템 자동 추천)
"""
import streamlit as st
import pandas as pd
import datetime, os, sys, json, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from lib import loader, db, perf, costs

st.set_page_config(page_title='자동매매 시뮬레이션', page_icon='🤖', layout='wide')
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

    # ─── 신규 시뮬레이션 생성 ─────────────────────────
    with col_a:
        st.markdown('### ➕ 새 시뮬레이션 시작')
        with st.form('new_sim'):
            sim_name = st.text_input('시뮬레이션 이름', value=f'v8 적극투자형 {datetime.date.today().strftime("%y%m%d")}')
            start_date = st.date_input('시작 일자', value=datetime.date.today())
            start_capital_eok = st.number_input('시작 자본 (억원)', min_value=0.1, value=1.0, step=0.1)
            notes = st.text_area('메모', value='시스템 자동 추천 포트폴리오 추종', height=80)
            submitted = st.form_submit_button('🚀 시뮬레이션 시작', type='primary', use_container_width=True)
            if submitted:
                # entry_order_plan.json 로드
                entry_plan = loader.load('entry_plan')
                if not entry_plan or not entry_plan.get('orders_1st'):
                    st.error('entry_order_plan.json 없음. `build_entry_order_plan.py --use-final` 먼저 실행 필요.')
                else:
                    sim_id = db.create_simulation(
                        name=sim_name,
                        start_date=start_date.strftime('%Y%m%d'),
                        start_capital=start_capital_eok * 1e8,
                        notes=notes,
                    )
                    # 1차 매수 일괄 등록 (키움 매매비용 자동 차감)
                    n_orders = 0
                    for o in entry_plan['orders_1st']:
                        if o.get('first_shares', 0) > 0:
                            mi = o.get('matched_ideas') or []
                            if o.get('is_default_pick'):
                                theme_id = 'default'
                            elif mi:
                                theme_id = f'#{mi[0]}'
                            else:
                                theme_id = None
                            price = float(o['close'])
                            shares = int(o['first_shares'])
                            c = costs.calc_trade_cost(price, shares, 'BUY')
                            db.add_sim_trade(
                                sim_id=sim_id,
                                trade_date=start_date.strftime('%Y%m%d'),
                                ticker=o['ticker'], name=o['name'],
                                action='BUY', shares=shares,
                                price=price,
                                fee=c['fee'] + c['slippage'], tax=c['tax'],
                                theme_id=theme_id,
                                signal_type='ENTRY_1ST',
                                note=f'1차 매수 (비중 {o["capital_weight_pct"]:.2f}%) | 수수료+슬리피지 {c["fee"]+c["slippage"]:,}원',
                            )
                            n_orders += 1
                    st.success(f'✅ 시뮬레이션 #{sim_id} 시작! 1차 매수 {n_orders}건 (키움 매매비용 자동 반영)')
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
                            applied = 0
                            for s in daily.get('signals', []):
                                if s['signal'] == 'WATCH':
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
                            st.success(f'✅ {today} 시그널 {applied}건 적용 (키움 매매비용 자동 반영)')
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
            total_cost = sum(p['cost'] for p in evals)
            total_mkt = sum(p['market_value'] for p in evals)
            total_pnl = total_mkt - total_cost
            total_pnl_pct = (total_mkt / total_cost - 1) * 100 if total_cost > 0 else 0

            col1, col2, col3, col4 = st.columns(4)
            col1.metric('시작 자본', f"{sim['start_capital']/1e8:.2f}억")
            col2.metric('투입 금액', f'{total_cost:,.0f}원')
            col3.metric('현재 평가', f'{total_mkt:,.0f}원')
            col4.metric('수익률', f'{total_pnl:,.0f}원', f'{total_pnl_pct:+.2f}%')

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
                # 신용이자: 시작 자본 = base, equity 125% → 자기자본 80%만 본인 자본
                base_cap = sim['start_capital']  # 시뮬레이션 시작 자본 = 자기자본 가정
                daily_v = perf.daily_portfolio_value(sim_trades, price_history, eval_dates,
                                                      credit_interest_pct=costs.CREDIT_INTEREST_PCT_ANNUAL,
                                                      base_capital=base_cap)
                metrics = perf.calc_perf_metrics(daily_v, kospi)
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

                # 비용 내역
                cost_cols = st.columns(3)
                cost_cols[0].metric('💰 누적 배당', f"{last_v.get('dividend_cum', 0):,.0f}원")
                cost_cols[1].metric('💸 누적 신용이자', f"{last_v.get('credit_interest_cum', 0):,.0f}원")
                # 매매비용 합
                trade_costs = sum((t.get('fee') or 0) + (t.get('tax') or 0) for t in sim_trades)
                cost_cols[2].metric('🧾 매매비용 합 (수수료+세금)', f"{trade_costs:,.0f}원")

                # 라인 차트
                df_daily = pd.DataFrame([
                    {'date': d, 'total_value': v['total_value'], 'cost_basis': v['cost_basis']}
                    for d, v in daily_v.items()
                ])
                df_daily['date_dt'] = pd.to_datetime(df_daily['date'])
                df_daily = df_daily.sort_values('date_dt')
                df_daily['시뮬레이션 수익률(%)'] = (df_daily['total_value'] / df_daily['cost_basis'] - 1) * 100

                chart_df = df_daily[['date_dt', '시뮬레이션 수익률(%)']].set_index('date_dt')

                if kospi:
                    kdf = pd.DataFrame([{'date_dt': pd.to_datetime(d), 'close': v} for d, v in kospi.items()])
                    kdf = kdf.sort_values('date_dt')
                    # 첫 평가일 종가 기준 정규화
                    asof_first = df_daily.iloc[0]['date_dt']
                    kdf_filtered = kdf[kdf['date_dt'] >= asof_first]
                    if not kdf_filtered.empty:
                        kdf_filtered = kdf_filtered.copy()
                        first_k = kdf_filtered.iloc[0]['close']
                        kdf_filtered['KOSPI 수익률(%)'] = (kdf_filtered['close'] / first_k - 1) * 100
                        kdf_idx = kdf_filtered.set_index('date_dt')[['KOSPI 수익률(%)']]
                        chart_df = chart_df.join(kdf_idx, how='outer').fillna(method='ffill')

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
